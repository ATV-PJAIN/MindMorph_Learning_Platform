"""Material ingestion (P2 #9): PDF extract → per-user RAG store → grounds the content graph.

PDF extraction runs the real PyMuPDF (local, no network). Embeddings are a DETERMINISTIC fake
(monkeypatched) so the suite never downloads the FastEmbed model and ranking is reproducible."""
import os
import re
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

import pytest
from langchain_core.embeddings import Embeddings

from graph.content_graph import build_content_graph
from rag import registry

_DIM = 256


def _vec(text):
    v = [0.0] * _DIM
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        v[zlib.crc32(tok.encode()) % _DIM] += 1.0
    return v


class _FakeEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [_vec(t) for t in texts]

    def embed_query(self, text):
        return _vec(text)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Fresh registry + fake embedder per test (no model download, no cross-test leakage)."""
    monkeypatch.setattr("rag.embeddings.get_embeddings", lambda *a, **k: _FakeEmbeddings())
    registry.reset()
    yield
    registry.reset()


def _make_pdf(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


# --- PDF extraction -----------------------------------------------------------------------------

def test_extract_text_from_pdf_bytes():
    from rag.pdf import extract_text_from_pdf

    out = extract_text_from_pdf(_make_pdf("Hello RAG ingestion world"))
    assert "Hello RAG ingestion world" in out


def test_extract_text_from_empty_pdf_raises():
    import fitz
    from rag.pdf import extract_text_from_pdf

    doc = fitz.open()
    doc.new_page()  # blank page, no text
    data = doc.tobytes()
    doc.close()
    with pytest.raises(ValueError):
        extract_text_from_pdf(data)


# --- per-user registry --------------------------------------------------------------------------

def test_ingest_pdf_populates_user_store_and_isolates_users():
    n = registry.ingest_pdf("alice", _make_pdf("mongodb express react node tutorial"), "mern.pdf")
    assert n >= 1

    alice = registry.get_user_store("alice")
    assert alice is not None and not alice.is_empty
    got = alice.retrieve("mongodb react node", k=1)
    assert "mongodb express react" in got and "Source: mern.pdf" in got

    # Bob never uploaded → no store (per-user isolation).
    assert registry.get_user_store("bob") is None


# --- content graph routes by user_id ------------------------------------------------------------

class _FakeContent:
    def __init__(self):
        self.calls = []

    def generate_content(self, q, fmt, remediation=None, context=None, primary_language=None):
        self.calls.append((q, fmt, remediation, context, primary_language))
        return "DRAFT"


class _FakeFactual:
    def gather_facts(self, q):
        return "WEBFACTS"


class _FakeSynth:
    def synthesize(self, q, draft, findings):
        return f"FINAL::{findings}"


def test_content_graph_uses_uploaded_material_for_that_user():
    registry.ingest_pdf("u1", _make_pdf("kubernetes pods deployments services networking"), "k8s.pdf")
    g = build_content_graph(content=_FakeContent(), factual=_FakeFactual(), synthesizer=_FakeSynth())

    out = g.invoke({"user_query": "kubernetes pods networking", "format_type": "B", "user_id": "u1"})
    ff = out["factual_findings"]
    assert "kubernetes pods deployments" in ff and "WEBFACTS" in ff      # KB + web merged
    assert "Source: k8s.pdf" in ff

    # A different user with no uploads → web only (no KB), RAG off globally.
    out2 = g.invoke({"user_query": "kubernetes", "format_type": "B", "user_id": "nobody"})
    assert out2["factual_findings"] == "WEBFACTS"


# --- HTTP endpoint ------------------------------------------------------------------------------

def test_ingest_endpoint_accepts_pdf():
    from fastapi.testclient import TestClient
    from api.main import app

    client = TestClient(app)
    pdf = _make_pdf("graphql schema resolvers queries mutations")
    r = client.post(
        "/users/u9/knowledge",
        files={"file": ("notes.pdf", pdf, "application/pdf")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"] == "notes.pdf" and body["chunks"] >= 1
    assert registry.get_user_store("u9") is not None


def test_ingest_endpoint_rejects_non_pdf():
    from fastapi.testclient import TestClient
    from api.main import app

    client = TestClient(app)
    r = client.post("/users/u9/knowledge", files={"file": ("notes.txt", b"hi", "text/plain")})
    assert r.status_code == 400
