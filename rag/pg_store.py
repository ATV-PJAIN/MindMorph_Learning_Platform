"""pgvector-backed per-user RAG store (P2 #9 persistence).

Same surface as the in-memory ``RagStore`` (``add_texts`` / ``retrieve`` / ``is_empty``) but durable:
each chunk + its FastEmbed vector is a row in ``rag_chunks`` scoped to ``user_id``; retrieval is a
cosine-distance query filtered to that user. SQLAlchemy/embeddings are imported lazily so importing
this module needs no DB and never loads fastembed.
"""
import logging
from typing import List, Optional

from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)

DEFAULT_K = 4


class PgRagStore:
    """A per-user, Postgres-persisted vector store handle (stateless — the data lives in the DB)."""

    def __init__(self, user_id: str, embeddings: Optional[Embeddings] = None):
        self.user_id = user_id
        self._embeddings = embeddings

    def _emb(self) -> Embeddings:
        if self._embeddings is None:
            import rag.embeddings as emb

            self._embeddings = emb.get_embeddings()
        return self._embeddings

    def add_texts(self, texts: List[str], metadatas: Optional[List[dict]] = None) -> None:
        if metadatas is not None and len(metadatas) != len(texts):
            raise ValueError("Passage metadata count does not match text count")
        pairs = [(t, m) for t, m in zip(texts, metadatas or [{} for _ in texts]) if t and t.strip()]
        texts = [t for t, _ in pairs]
        if not texts:
            return
        from persistence.db import get_sessionmaker
        from persistence.models import RagChunkRow

        vectors = self._emb().embed_documents(texts)
        if len(vectors) != len(texts):
            raise ValueError("Embedding count does not match passage count")
        metadatas = [m for _, m in pairs]
        rows = [
            RagChunkRow(
                user_id=self.user_id,
                source=(m or {}).get("source", "upload"),
                source_id=(m or {}).get("source_id"),
                page=(m or {}).get("page"),
                content=t,
                embedding=v,
            )
            for t, v, m in zip(texts, vectors, metadatas)
        ]
        with get_sessionmaker()() as session:
            session.add_all(rows)
            session.commit()
        logger.info("RAG(pg): stored %d chunk(s)", len(rows))

    @property
    def is_empty(self) -> bool:
        from sqlalchemy import func, select

        from persistence.db import get_sessionmaker
        from persistence.models import RagChunkRow

        with get_sessionmaker()() as session:
            n = session.execute(
                select(func.count())
                .select_from(RagChunkRow)
                .where(RagChunkRow.user_id == self.user_id)
            ).scalar_one()
        return n == 0

    def search(self, query: str, k: int = DEFAULT_K, source_ids: Optional[list[str]] = None) -> list[dict]:
        if not query.strip() or source_ids == []:
            return []
        from sqlalchemy import select

        from persistence.db import get_sessionmaker
        from persistence.models import RagChunkRow

        qvec = self._emb().embed_query(query)
        distance = RagChunkRow.embedding.cosine_distance(qvec)
        statement = select(RagChunkRow, distance.label("distance")).where(RagChunkRow.user_id == self.user_id)
        if source_ids is not None:
            statement = statement.where(RagChunkRow.source_id.in_(source_ids))
        with get_sessionmaker()() as session:
            rows = session.execute(statement.order_by(distance).limit(min(max(k, 0), 20))).all()
            return [{"id": f"p_{row.id}", "text": row.content, "source": row.source,
                     "source_id": row.source_id, "page": row.page, "score": 1.0 - float(dist)}
                    for row, dist in rows]

    def retrieve(self, query: str, k: int = DEFAULT_K) -> Optional[str]:
        try:
            rows = self.search(query, k)
        except Exception:
            logger.warning("RAG(pg): retrieval unavailable")
            return None
        if not rows:
            return None
        return "\n".join(f"- {row['text']}\n  Source: {row['source']}" for row in rows)
