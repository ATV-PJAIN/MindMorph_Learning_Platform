"""Adversarial evidence, privacy and provenance contracts; no live models or services."""
import asyncio
import logging
from unittest.mock import Mock

import pytest
from langchain_core.embeddings import Embeddings

import config
from rag import registry
import rag.grounding as grounding
from rag.grounding import Evidence, Passage, CitationStream, evidence_prompt, retrieve_evidence, validate_citations
from rag.store import RagStore, _chunk_text


class TopicEmbeddings(Embeddings):
    def embed_query(self, text):
        text = text.lower()
        return [float(any(t in text for t in ("sql", "join", "database"))),
                float(any(t in text for t in ("guitar", "chord", "music"))),
                float(any(t in text for t in ("garden", "plant", "compost")))]

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("MINDMORPH_STORE", "memory")
    monkeypatch.setenv("MINDMORPH_WEB_RESEARCH", "0")
    monkeypatch.setenv("MINDMORPH_RERANK", "0")
    monkeypatch.setattr(config, "RAG_ENABLED", False)
    monkeypatch.setattr("rag.embeddings.get_embeddings", lambda *a, **kw: TopicEmbeddings())
    registry.reset()
    yield
    registry.reset()


def add(user, text, filename="notes.pdf", source_id="doc"):
    store = registry.get_user_store(user, create=True)
    store.add_texts([text], [{"source": filename, "source_id": source_id, "page": 3}])
    return store


def test_unrelated_personal_material_does_not_suppress_relevant_shared(monkeypatch):
    add("alice", "garden compost tips")
    shared = RagStore(TopicEmbeddings())
    shared.add_texts(["SQL joins combine database rows"], [{"source": "sql-guide.md"}])
    monkeypatch.setattr(config, "RAG_ENABLED", True)
    monkeypatch.setattr(registry, "get_global_store", lambda: shared)
    result = retrieve_evidence("SQL joins", "alice")
    assert [(p["kind"], p["source"]) for p in result["passages"]] == [("shared", "sql-guide.md")]
    assert result["sources"]["personal"] == "no_match"


def test_private_material_never_crosses_learners_or_explicit_empty_filter():
    add("alice", "SQL joins private ALICE")
    add("bob", "SQL joins private BOB")
    assert "ALICE" in evidence_prompt(retrieve_evidence("SQL joins", "alice"))
    assert "BOB" not in evidence_prompt(retrieve_evidence("SQL joins", "alice"))
    assert not retrieve_evidence("SQL joins", None)["passages"]
    assert not retrieve_evidence("SQL joins", "alice", source_ids=[])["passages"]
    assert not retrieve_evidence("SQL joins", "alice", source_ids=["foreign"])["passages"]


def test_duplicate_content_and_formatted_evidence_budget(monkeypatch):
    store = add("alice", "SQL joins duplicate")
    store.add_texts(["SQL joins duplicate"] * 8, [{"source": "repeat.pdf"}] * 8)
    store.add_texts([f"SQL joins {'x' * 3000} {i}" for i in range(15)],
                    [{"source": "long-name" * 80}] * 15)
    result = retrieve_evidence("SQL joins", "alice")
    assert len(result["passages"]) <= 6
    assert result["candidate_count"] <= 20
    assert len(evidence_prompt(result)) <= 8000
    texts = [p["text"] for p in result["passages"]]
    assert len(texts) == len(set(texts))


def test_model_failure_uses_bounded_similarity_fallback_and_reports_it(monkeypatch):
    add("alice", "SQL joins practical examples")
    add("alice", "guitar chords", source_id="other")
    monkeypatch.setenv("MINDMORPH_RERANK", "1")
    monkeypatch.setattr(grounding, "_reranker", lambda: None)
    result = retrieve_evidence("SQL joins", "alice")
    assert result["status"] == "insufficient"
    assert result["warnings"] == ["ranking_unavailable"]
    assert all("guitar" not in p["text"] for p in result["passages"])


def test_no_match_disabled_and_unavailable_are_distinct(monkeypatch):
    assert retrieve_evidence("SQL joins")["status"] == "disabled"
    add("alice", "garden compost")
    assert retrieve_evidence("SQL joins", "alice")["status"] == "insufficient"
    monkeypatch.setattr(registry, "get_user_store", Mock(side_effect=RuntimeError("private diagnostic")))
    result = retrieve_evidence("SQL joins", "alice")
    assert result["status"] == "unavailable"
    assert "private diagnostic" not in str(result)


def test_private_query_and_filename_are_not_sent_to_public_research(monkeypatch):
    import rag.public_web
    search = Mock(return_value=([{"text": "SQL joins public reference", "source": "SQL reference",
                                  "url": "https://example.org/sql", "kind": "web_snippet"}], "ok"))
    monkeypatch.setattr(rag.public_web, "research_web", search)
    monkeypatch.setenv("MINDMORPH_WEB_RESEARCH", "1")
    add("alice", "SQL joins SECRET", "private-name.pdf")
    result = retrieve_evidence("SECRET from private-name.pdf: SQL joins", "alice", public_query="SQL joins")
    search.assert_called_once_with("SQL joins")
    assert {p["kind"] for p in result["passages"]} == {"personal", "web_snippet"}


def test_new_pdf_pages_and_same_filename_source_identity():
    import fitz
    document = fitz.open()
    for text in ("SQL joins page one", "", "SQL joins page three"):
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    registry.ingest_pdf("alice", data, "same.pdf")
    registry.ingest_pdf("alice", data, "same.pdf")
    rows = registry.get_user_store("alice").search("SQL joins", k=8)
    assert {r["page"] for r in rows} == {1, 3}
    assert len({r["source_id"] for r in rows}) == 2
    assert len({r["id"] for r in rows}) == 4
    assert all(len(chunk) <= 1000 for chunk in _chunk_text("x" * 2501))


def test_valid_citation_kept_invalid_removed_even_across_stream_chunks():
    bundle = Evidence(status="grounded", passages=[Passage(id="p_ok", text="SQL joins", source="notes.pdf", kind="personal")]).model_dump()
    text, result = validate_citations("Fact [source:p_ok]. False [source:fake].", bundle)
    assert "[source:p_ok]" in text and "[source:fake]" not in text
    assert result["status"] == "insufficient" and "unsupported_citation" in result["warnings"]
    stream = CitationStream(bundle)
    output = "".join(stream.push(part) for part in ["Fact [sou", "rce:p_ok]. False [source:", "fake", "]."])
    output += stream.push("", final=True)
    assert output == text


def test_privacy_redacts_logs_and_trace_context_and_telemetry(monkeypatch, caplog):
    from langsmith import get_tracing_context
    from services.generation_privacy import private_context
    from services.telemetry import emit_telemetry
    sink = Mock(return_value={"status": "skipped"})
    monkeypatch.setattr("services.telemetry.emit_event_to_kafka", sink)
    with caplog.at_level(logging.WARNING), private_context():
        assert get_tracing_context()["enabled"] is False
        try:
            raise ValueError("PRIVATE PASSAGE")
        except ValueError:
            logging.getLogger("privacy-probe").exception("PRIVATE SOURCE")
        emit_telemetry({"stage": "grounding", "candidate_count": 3, "passage": "PRIVATE PASSAGE",
                        "source": "PRIVATE SOURCE", "focus": ["PRIVATE"], "query": "PRIVATE"})
    assert "PRIVATE" not in caplog.text
    assert sink.call_args.args[0] == {"stage": "grounding", "candidate_count": 3}


def test_invented_prose_links_are_rejected_but_literal_code_stays_intact():
    bundle = Evidence(status="grounded", passages=[Passage(id="real", text="SQL", source="Reference",
                        kind="web_page", url="https://example.org/sql")]).model_dump()
    text = "Source https://invented.example/claim and https://example.org/sql. `https://code.example/api`"
    validated, result = validate_citations(text, bundle)
    assert "https://invented.example" not in validated
    assert "https://example.org/sql" in validated and "https://code.example/api" in validated
    assert result["status"] == "insufficient"
    stream = CitationStream(bundle)
    rendered = "".join(stream.push(part) for part in ["Source htt", "ps://invented.example/claim", " and "])
    rendered += stream.push("", final=True)
    assert "invented.example" not in rendered
    assert "INVENTED.EXAMPLE" not in validate_citations("HTTPS://INVENTED.EXAMPLE/claim", bundle)[0]
    assert validate_citations("[SOURCE:real]", bundle)[0] == "[source:real]"


def test_planning_reranks_against_goal_instead_of_research_instructions(monkeypatch):
    from agents.grounding.knowledge_grounding_agent import KnowledgeGroundingAgent
    add("alice", "SQL joins match database rows")
    goal = "Learn SQL joins"
    reranker = Mock()
    reranker.rerank.side_effect = lambda query, documents, **kw: [5.0 if query == goal else -2.0 for _ in documents]
    monkeypatch.setenv("MINDMORPH_RERANK", "1")
    monkeypatch.setattr(grounding, "_reranker", lambda: reranker)
    result = KnowledgeGroundingAgent().gather("What authoritative evidence explains SQL joins for beginners?", "alice", public_query=goal)
    assert result["passages"] and result["status"] == "grounded"
    assert reranker.rerank.call_args.args[0] == goal


def test_network_library_threads_cannot_log_query_bodies(caplog):
    from concurrent.futures import ThreadPoolExecutor
    with caplog.at_level(logging.WARNING), ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(logging.getLogger("primp").warning, "response URL contains PRIVATE QUERY").result()
    assert "PRIVATE QUERY" not in caplog.text


async def test_private_stream_can_be_closed_from_another_task_without_context_leak():
    from langsmith import get_tracing_context
    from services.generation_privacy import private_generation, _private
    closed = []

    @private_generation
    async def events():
        try:
            assert _private.get() is True
            assert get_tracing_context()["enabled"] is False
            yield "stage"
        finally:
            closed.append(True)

    stream = events()
    assert await stream.__anext__() == "stage"
    assert _private.get() is False
    await asyncio.create_task(stream.aclose())
    assert closed == [True] and _private.get() is False


def test_session_repository_sql_debug_cannot_log_saved_private_evidence(caplog):
    from persistence.repository import _storage_errors
    from sqlalchemy.exc import SQLAlchemyError

    @_storage_errors
    def operation():
        logging.getLogger("sqlalchemy.engine").warning("PARAMETERS: PRIVATE STORED PASSAGE")
        raise SQLAlchemyError("PRIVATE STORED PASSAGE")

    from persistence.errors import StorageUnavailableError
    with caplog.at_level(logging.WARNING), pytest.raises(StorageUnavailableError):
        operation()
    assert "PRIVATE STORED PASSAGE" not in caplog.text
