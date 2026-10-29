"""RAG store + content-graph grounding — driven by a DETERMINISTIC fake embedder (hashing
bag-of-words), so the suite never downloads the FastEmbed model and ranking is reproducible."""
import os
import re
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

from langchain_core.embeddings import Embeddings

from graph.content_graph import _merge_findings, build_content_graph
from rag.store import RagStore, _chunk_text

_DIM = 256


def _vec(text: str):
    """Hashing bag-of-words: cosine grows with shared tokens — enough to force a deterministic top-1."""
    v = [0.0] * _DIM
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        v[zlib.crc32(tok.encode()) % _DIM] += 1.0  # stable across processes (builtin hash isn't)
    return v


class _FakeEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [_vec(t) for t in texts]

    def embed_query(self, text):
        return _vec(text)


# --- _merge_findings (back-compat is load-bearing) ---------------------------------------------

def test_merge_single_source_is_verbatim():
    assert _merge_findings(None, "WEB") == "WEB"      # web-only / RAG-off path unchanged
    assert _merge_findings("KB", None) == "KB"
    assert _merge_findings(None, None) is None


def test_merge_both_sources_are_labelled():
    out = _merge_findings("kbtext", "webtext")
    assert "From the knowledge base:" in out and "kbtext" in out
    assert "From the web:" in out and "webtext" in out


# --- RagStore ----------------------------------------------------------------------------------

def test_add_and_retrieve_returns_chunk_with_source():
    store = RagStore(_FakeEmbeddings())
    store.add_texts(
        ["mongodb express react node full stack", "completely unrelated gardening tips"],
        metadatas=[{"source": "mern.md"}, {"source": "garden.md"}],
    )
    out = store.retrieve("mongodb react node stack", k=1)
    assert out is not None
    assert "mongodb express react" in out
    assert "Source: mern.md" in out


def test_empty_store_retrieve_returns_none():
    assert RagStore(_FakeEmbeddings()).retrieve("anything") is None


def test_from_knowledge_dir_indexes_seed_corpus():
    store = RagStore.from_knowledge_dir(
        os.path.join(ROOT, "knowledge_base"), embeddings=_FakeEmbeddings()
    )
    assert store is not None and not store.is_empty
    out = store.retrieve("MERN MongoDB Express React Node", k=2)
    assert out is not None and "Source: mern-stack.md" in out


def test_from_knowledge_dir_missing_returns_none():
    assert RagStore.from_knowledge_dir(os.path.join(ROOT, "no_such_dir")) is None


def test_chunker_caps_size():
    text = "\n\n".join(["x" * 400] * 5)  # 5 paragraphs of 400 chars
    chunks = _chunk_text(text, max_chars=1000)
    assert len(chunks) >= 2 and all(len(c) <= 1000 + 4 for c in chunks)


# --- content graph wiring ----------------------------------------------------------------------

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
    """Echo factual_findings so we can assert what reached the synthesizer."""
    def synthesize(self, q, draft, findings):
        return f"FINAL::{findings}"


class _FakeRetriever:
    def retrieve(self, q, k=4):
        return "KBFACTS"


def test_factual_node_merges_kb_and_web():
    g = build_content_graph(
        content=_FakeContent(), factual=_FakeFactual(),
        synthesizer=_FakeSynth(), retriever=_FakeRetriever(),
    )
    out = g.invoke({"user_query": "mern", "format_type": "B"})
    ff = out["factual_findings"]
    assert "KBFACTS" in ff and "WEBFACTS" in ff
    assert "From the knowledge base:" in ff


def test_factual_node_web_only_when_no_retriever():
    g = build_content_graph(
        content=_FakeContent(), factual=_FakeFactual(), synthesizer=_FakeSynth(), retriever=None,
    )
    out = g.invoke({"user_query": "mern", "format_type": "B"})
    assert out["factual_findings"] == "WEBFACTS"  # verbatim — no RAG, no label
