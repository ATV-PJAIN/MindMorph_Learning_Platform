"""Real migrated pgvector provenance and ownership; required by the disposable storage runner."""
import json
import os
import subprocess
import sys
import uuid

import pytest
from langchain_core.embeddings import Embeddings


class Fixed384(Embeddings):
    def embed_query(self, query):
        return [1.0] + [0.0] * 383

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]


@pytest.fixture(autouse=True)
def migrated_database(monkeypatch):
    from persistence.db import check_ready
    try:
        check_ready()
    except Exception:
        if os.getenv("MINDMORPH_REQUIRE_DB_TESTS") == "1":
            raise
        pytest.skip("A migrated disposable PostgreSQL/pgvector database is required")
    monkeypatch.setenv("MINDMORPH_STORE", "postgres")
    monkeypatch.setattr("rag.embeddings.get_embeddings", lambda *a, **k: Fixed384())


def test_pdf_pages_and_source_identity_survive_fresh_process(monkeypatch):
    import fitz
    from rag import registry
    from rag.pg_store import PgRagStore
    user = "grounding-db-" + uuid.uuid4().hex
    document = fitz.open()
    for words in ("SQL joins first page", "", "SQL joins third page"):
        page = document.new_page()
        if words:
            page.insert_text((72, 72), words)
    data = document.tobytes()
    document.close()
    assert registry.ingest_pdf(user, data, "same.pdf") == 2
    assert registry.ingest_pdf(user, data, "same.pdf") == 2
    rows = PgRagStore(user, Fixed384()).search("SQL joins", 8)
    assert {r["page"] for r in rows} == {1, 3}
    assert len({r["source_id"] for r in rows}) == 2
    source_ids = [r["source_id"] for r in rows]
    code = '''import json,sys
from langchain_core.embeddings import Embeddings
from rag.pg_store import PgRagStore
class Fixed(Embeddings):
    def embed_query(self, query): return [1.0]+[0.0]*383
    def embed_documents(self, texts): return [self.embed_query(t) for t in texts]
user=json.load(sys.stdin)
print(json.dumps(PgRagStore(user, Fixed()).search('SQL joins',8)))
'''
    process = subprocess.run([sys.executable, "-c", code], input=json.dumps(user), text=True,
                             capture_output=True, timeout=30, env=os.environ.copy())
    assert process.returncode == 0, process.stderr[-1000:]
    restored = json.loads(process.stdout.strip().splitlines()[-1])
    assert {r["id"] for r in restored} == {r["id"] for r in rows}
    assert {r["source_id"] for r in restored} == set(source_ids)


def test_document_filter_cannot_cross_tenant_even_with_known_foreign_source():
    from rag.pg_store import PgRagStore
    first, second = "grounding-db-" + uuid.uuid4().hex, "grounding-db-" + uuid.uuid4().hex
    a, b = PgRagStore(first, Fixed384()), PgRagStore(second, Fixed384())
    a.add_texts(["SQL ALICE"], [{"source": "same.pdf", "source_id": "alice-source", "page": 2}])
    b.add_texts(["SQL BOB"], [{"source": "same.pdf", "source_id": "bob-source", "page": 1}])
    assert a.search("SQL", source_ids=["bob-source"]) == []
    assert a.search("SQL", source_ids=[]) == []
    assert all(r["text"] == "SQL ALICE" for r in a.search("SQL", source_ids=["alice-source"]))
    assert all(r["text"] == "SQL BOB" for r in b.search("SQL"))


def test_short_embedding_batch_cannot_publish_partial_upload():
    from rag.pg_store import PgRagStore

    class Short(Fixed384):
        def embed_documents(self, texts):
            return [self.embed_query("SQL")]

    user = "grounding-db-" + uuid.uuid4().hex
    store = PgRagStore(user, Short())
    with pytest.raises(ValueError, match="Embedding count"):
        store.add_texts(["SQL first", "SQL second"])
    assert store.is_empty
