"""In-memory RAG vector store + corpus loader.

Wraps ``langchain_core`` ``InMemoryVectorStore`` (no extra dependency). Retrieval output mirrors
``FactualAgent.gather_facts`` shape (``- {chunk}\\n  Source: {src}``) so the synthesizer prompt is
unchanged. Swap the store for pgvector later (Postgres is already in the stack) without touching callers.
"""
import logging
import os
from pathlib import Path
from threading import RLock
from typing import List, Optional

from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import InMemoryVectorStore

logger = logging.getLogger(__name__)

DEFAULT_K = 4
MAX_CHUNK_CHARS = 1000
KNOWLEDGE_EXTENSIONS = (".md", ".txt")


def _chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> List[str]:
    """Split on blank lines, then pack paragraphs into chunks capped at ``max_chars``."""
    if max_chars < 1:
        raise ValueError("Chunk size must be positive")
    paragraphs = [p.strip()[i:i + max_chars] for p in text.split("\n\n") if p.strip()
                  for i in range(0, len(p.strip()), max_chars)]
    chunks: List[str] = []
    buf = ""
    for para in paragraphs:
        if buf and len(buf) + len(para) + 2 > max_chars:
            chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    return chunks


class RagStore:
    """A thin retrieval wrapper over an in-memory embedded corpus."""

    def __init__(self, embeddings: Embeddings):
        self._vs = InMemoryVectorStore(embeddings)
        self._count = 0
        self._lock = RLock()

    def add_texts(self, texts: List[str], metadatas: Optional[List[dict]] = None) -> None:
        if metadatas is not None and len(metadatas) != len(texts):
            raise ValueError("Passage metadata count does not match text count")
        pairs = [(t, m) for t, m in zip(texts, metadatas or [{} for _ in texts]) if t and t.strip()]
        texts = [t for t, _ in pairs]
        if not texts:
            return
        with self._lock:
            self._vs.add_texts(texts, metadatas=[m for _, m in pairs])
            self._count += len(texts)

    def search(self, query: str, k: int = DEFAULT_K, source_ids: Optional[list[str]] = None) -> list[dict]:
        """Scored passages; an explicit empty selection never broadens to all material."""
        if not query.strip() or self.is_empty or source_ids == []:
            return []
        allowed = set(source_ids) if source_ids is not None else None
        with self._lock:
            matches = self._vs.similarity_search_with_score(
                query, k=min(max(k, 0), 20),
                filter=(lambda d: (d.metadata or {}).get("source_id") in allowed) if allowed is not None else None,
            )
        return [{"id": "p_" + str(doc.id), "text": doc.page_content,
                 "source": (doc.metadata or {}).get("source", "knowledge base"),
                 "source_id": (doc.metadata or {}).get("source_id"),
                 "page": (doc.metadata or {}).get("page"), "score": float(score)}
                for doc, score in matches]

    @property
    def is_empty(self) -> bool:
        return self._count == 0

    def retrieve(self, query: str, k: int = DEFAULT_K) -> Optional[str]:
        """Return the top-k chunks formatted like ``gather_facts``, or ``None`` if nothing matches."""
        if not query or not query.strip() or self.is_empty:
            return None
        try:
            docs = self.search(query, k)
        except Exception:
            logger.exception("RAG: retrieval failed for %r", query)
            return None
        if not docs:
            return None
        blocks = []
        for d in docs:
            blocks.append(f"- {d['text']}\n  Source: {d['source']}")
        return "\n".join(blocks)

    @classmethod
    def from_knowledge_dir(
        cls, path: str, embeddings: Optional[Embeddings] = None
    ) -> Optional["RagStore"]:
        """Build a store from ``*.md``/``*.txt`` files under ``path``. Returns ``None`` if the dir is
        missing or yields no chunks (so callers degrade to web-only grounding)."""
        base = Path(path)
        if not base.is_dir():
            logger.info("RAG: knowledge dir %s not found; RAG disabled", path)
            return None
        if embeddings is None:
            from rag.embeddings import get_embeddings

            embeddings = get_embeddings()
        store = cls(embeddings)
        for file in sorted(base.rglob("*")):
            if file.suffix.lower() not in KNOWLEDGE_EXTENSIONS:
                continue
            if file.name.lower() == "readme.md":
                continue  # the dir's own docs, not corpus
            try:
                text = file.read_text(encoding="utf-8")
            except Exception:
                logger.exception("RAG: failed reading %s", file)
                continue
            chunks = _chunk_text(text)
            source = os.path.relpath(file, base)
            store.add_texts(chunks, metadatas=[{"source": source}] * len(chunks))
        if store.is_empty:
            logger.info("RAG: knowledge dir %s has no ingestible files; RAG disabled", path)
            return None
        logger.info("RAG: indexed %d chunk(s) from %s", store._count, path)
        return store
