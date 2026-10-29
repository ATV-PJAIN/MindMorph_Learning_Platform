"""DB-gated integration test for the pgvector-backed PgRagStore (P2 #9 persistence).

Skipped automatically when Postgres+pgvector at config.DATABASE_URL is unreachable (CI / no
`docker compose up -d db`), so the default suite stays infra-free. Uses a deterministic 384-dim fake
embedder (matches BAAI/bge-small-en-v1.5 width) so no model download is needed.

Run explicitly:
    docker compose up -d db && conda run -n mindmorph alembic upgrade head
    conda run -n mindmorph python -m pytest tests/test_rag_pgvector.py -q
"""
import os
import re
import sys
import uuid
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

import pytest
from langchain_core.embeddings import Embeddings

from rag.embeddings import EMBED_DIM


def _vec(text: str):
    v = [0.0] * EMBED_DIM
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        v[zlib.crc32(tok.encode()) % EMBED_DIM] += 1.0
    # cosine_distance needs a non-zero vector; nudge an unused slot so empty text still embeds.
    if not any(v):
        v[0] = 1.0
    return v


class _Fake384(Embeddings):
    def embed_documents(self, texts):
        return [_vec(t) for t in texts]

    def embed_query(self, text):
        return _vec(text)


def _pg_ready() -> bool:
    try:
        from persistence.db import check_ready
        check_ready()  # Migrations must actually have run; never create schema in a readiness test.
        return True
    except Exception:
        if os.getenv("MINDMORPH_REQUIRE_DB_TESTS") == "1":
            raise RuntimeError("Required migrated pgvector database is unavailable")
        return False


pytestmark = pytest.mark.skipif(
    not _pg_ready(),
    reason="Postgres+pgvector not reachable (run `docker compose up -d db && alembic upgrade head`)",
)


def test_pg_ingest_retrieve_and_user_isolation():
    from rag.pg_store import PgRagStore

    ua, ub = f"u-{uuid.uuid4().hex}", f"u-{uuid.uuid4().hex}"
    a = PgRagStore(ua, embeddings=_Fake384())
    b = PgRagStore(ub, embeddings=_Fake384())

    assert a.is_empty
    a.add_texts(
        ["mongodb express react node full stack", "unrelated gardening compost tips"],
        metadatas=[{"source": "mern.pdf"}, {"source": "garden.pdf"}],
    )
    assert not a.is_empty

    got = a.retrieve("mongodb react node stack", k=1)
    assert got is not None
    assert "mongodb express react" in got and "Source: mern.pdf" in got

    # Bob never ingested → isolated + empty.
    assert b.is_empty
    assert b.retrieve("mongodb") is None


def test_registry_routes_to_pg_when_store_is_postgres(monkeypatch):
    monkeypatch.setenv("MINDMORPH_STORE", "postgres")
    monkeypatch.setattr("rag.embeddings.get_embeddings", lambda *a, **k: _Fake384())
    from rag import registry
    from rag.pg_store import PgRagStore

    uid = f"u-{uuid.uuid4().hex}"
    store = registry.get_user_store(uid)
    assert isinstance(store, PgRagStore)

    registry.ingest_pdf  # smoke: symbol exists
    n = registry.get_user_store(uid)  # stateless handle
    assert n.is_empty  # no uploads yet for this fresh user
