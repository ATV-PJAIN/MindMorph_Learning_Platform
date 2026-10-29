"""Registry of RAG vector stores (P2 #9).

Per-user store backend is selected by ``MINDMORPH_STORE`` (same switch as the session repository):
``postgres`` → durable ``PgRagStore`` (pgvector), anything else → process-memory ``RagStore``. Plus a
cached **global** store built from the shared knowledge base.

Embeddings are resolved via ``rag.embeddings.get_embeddings`` (imported lazily and indirectly so tests
can monkeypatch it with a fake — no model download).
"""
import logging
import os
import uuid
from threading import RLock
from typing import Optional

from rag.store import RagStore, _chunk_text

logger = logging.getLogger(__name__)

_user_stores: dict[str, RagStore] = {}
_global_store: Optional[RagStore] = None
_global_built = False
_store_lock = RLock()


def _use_postgres() -> bool:
    return os.getenv("MINDMORPH_STORE", "postgres").lower() == "postgres"


def _new_store() -> RagStore:
    import rag.embeddings as emb  # indirection so tests can patch get_embeddings

    return RagStore(emb.get_embeddings())


def get_user_store(user_id: str, create: bool = False):
    """Return the user's store. In Postgres mode this is a stateless ``PgRagStore`` handle (the data is
    in the DB, so ``create`` is irrelevant — emptiness is read from the DB). In memory mode it's a
    process-local ``RagStore``, created on first use when ``create=True``."""
    if not user_id:
        return None
    if _use_postgres():
        from rag.pg_store import PgRagStore

        return PgRagStore(user_id)
    with _store_lock:
        store = _user_stores.get(user_id)
        if store is None and create:
            store = _new_store()
            _user_stores[user_id] = store
    return store


def get_global_store() -> Optional[RagStore]:
    """Cached store built from ``config.KNOWLEDGE_DIR`` (shared seed corpus). None if empty/missing."""
    global _global_store, _global_built
    with _store_lock:
        if not _global_built:
            import config
            _global_store = RagStore.from_knowledge_dir(config.KNOWLEDGE_DIR)
            _global_built = True
    return _global_store


def ingest_pdf(user_id: str, data: bytes, filename: str) -> int:
    """Extract a PDF, chunk it, and add it to the user's store. Returns the chunk count."""
    from rag.pdf import extract_pages_from_pdf

    pages = extract_pages_from_pdf(data)
    source_id = uuid.uuid4().hex
    chunks, metadata = [], []
    for page, text in pages:
        parts = _chunk_text(text)
        chunks.extend(parts)
        metadata.extend({"source": filename, "source_id": source_id, "page": page} for _ in parts)
    store = get_user_store(user_id, create=True)
    store.add_texts(chunks, metadatas=metadata)
    logger.info("RAG: ingested %d chunk(s); %d page(s) without extractable text",
                len(chunks), sum(not text for _, text in pages))
    return len(chunks)


def reset() -> None:
    """Clear all stores (test isolation)."""
    global _global_store, _global_built
    _user_stores.clear()
    _global_store = None
    _global_built = False
