"""Grounded planning, authored-bundle reuse and streaming through the real route/service seams."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, Mock, MagicMock

import pytest
from fastapi.testclient import TestClient

import services.learning_service as service
from rag.grounding import Evidence, Passage


GRAPH = {"summary": "SQL joins", "nodes": [{"id": "joins", "label": "SQL joins",
         "description": "Join rows", "level": "foundational"}], "edges": []}


def bundle():
    return Evidence(status="grounded", passages=[Passage(id="p_local", text="SQL joins match related rows.",
                    kind="personal", source="private.pdf", page=2, score=0.9)]).model_dump()


@pytest.fixture(autouse=True)
def no_external_work(monkeypatch):
    monkeypatch.setenv("MINDMORPH_STORE", "memory")
    monkeypatch.setenv("MINDMORPH_NEO4J_MIRROR", "0")
    monkeypatch.setattr(service, "_run_assessment", lambda *a, **k: None)


async def test_four_branches_join_once_with_actual_evidence_and_caller(monkeypatch):
    import graph.learning_plan_graph as planning
    monkeypatch.setattr(planning, "_run_market", AsyncMock(return_value={"summary": "MARKET"}))
    monkeypatch.setattr(planning, "_fetch_github_repos", AsyncMock(return_value=None))
    routed = Mock()
    routed.route_query.return_value = Mock(Assigned_Agent="SCOUT", Reasoning="goal")
    scout = Mock()
    scout.generate_specialized_queries.return_value = {"sub_agent_queries": {"KNOWLEDGE": "SQL join concepts"}}
    academic = Mock()
    academic.provide_academic_roadmap.return_value = Mock(content="ACADEMIC")
    practical = Mock()
    practical.provide_practical_advice.return_value = Mock(content="PRACTICAL")
    knowledge = Mock()
    knowledge.gather.return_value = bundle()
    consensus = Mock()
    consensus.build_skill_graph.return_value = deepcopy(GRAPH)
    reviewer = Mock()
    reviewer.review_skill_graph.return_value = Mock(passed=False, notes="Check coverage")
    graph = planning.build_graph(orchestrator=routed, scout=scout, academic=academic, market=Mock(),
                                 practical=practical, consensus=consensus, reviewer=reviewer, grounding=knowledge)
    result = await graph.ainvoke({"user_query": "Learn SQL", "public_query": "Learn SQL", "user_id": "alice"})
    assert consensus.build_skill_graph.call_count == 1
    call = consensus.build_skill_graph.call_args
    assert call.args[1:] == ("ACADEMIC", "MARKET", "PRACTICAL")
    assert call.kwargs["knowledge_evidence"] == bundle()
    knowledge.gather.assert_called_once_with("SQL join concepts", "alice", public_query="Learn SQL")
    assert reviewer.review_skill_graph.call_args.kwargs["knowledge_evidence"] == bundle()
    assert result["review_passed"] is False and result["grounding"] == bundle()
    assert graph.checkpointer is None


@pytest.mark.parametrize("streaming", [False, True])
def test_actual_create_route_passes_identity_and_never_touches_shared_cache(monkeypatch, streaming):
    from api.main import app
    calls = []

    class Pipeline:
        async def ainvoke(self, state):
            calls.append(deepcopy(state))
            return {**state, "route": "SCOUT", "skill_graph": deepcopy(GRAPH), "grounding": bundle(),
                    "review_passed": False, "review_notes": "retained"}

        async def astream(self, state, **kwargs):
            yield {"reviewer": await self.ainvoke(state)}

    monkeypatch.setattr(service, "_get_orchestration_graph", lambda: Pipeline())
    monkeypatch.setattr(service, "_get_graph_from_cache", Mock(side_effect=AssertionError("shared read")))
    monkeypatch.setattr(service, "_save_graph_to_cache", Mock(side_effect=AssertionError("shared write")))
    client = TestClient(app)
    identities = []
    for user in ("grounding-alice", "grounding-bob"):
        response = client.post("/sessions/stream" if streaming else "/sessions", json={"user_id": user, "query": "Learn SQL"})
        assert response.status_code == 200
        if streaming:
            import json
            data = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")][-1]["session"]
        else:
            data = response.json()
        assert calls[-1]["user_id"] == user
        saved = client.get(f"/sessions/{user}/{data['session_id']}").json()["learning_session"]
        assert saved["grounding"] == bundle() and saved["review_notes"] == "retained"
        assert saved["lessons"] == {} and saved["public_query"] == "Learn SQL"
        identities.append(saved["graph_identity"])
    assert identities[0] != identities[1]


async def test_lesson_and_practice_share_one_frozen_evidence_bundle():
    from graph.content_graph import build_content_graph
    from graph.exercise_graph import build_exercise_graph
    from graph.lesson_graph import build_lesson_graph
    helper = Mock(return_value=bundle())
    content = Mock()
    content.generate_content.return_value = "Draft"
    synthesis = Mock()
    synthesis.synthesize.return_value = "Taught SQL joins [source:p_local]."
    content_graph = build_content_graph(content=content, synthesizer=synthesis, grounding_helper=helper)
    selector = Mock()
    selector.select_format.return_value = Mock(format="coding_challenge", language="sql", reasoning="practice")
    practice = Mock()
    practice.synthesize.return_value = "Practice SQL joins [source:p_local]."
    grader = Mock()
    grader.build_grading_artifact.return_value.model_dump.return_value = {"format": "coding_challenge", "language": "sql", "sql_assertions": ["SELECT 1"]}
    github, blog, dataset = AsyncMock(), Mock(), Mock()
    other_helper = Mock(side_effect=AssertionError("must reuse content evidence"))
    exercise_graph = build_exercise_graph(format_selector=selector, synthesizer=practice, grader=grader,
                                           github=github, blog=blog, dataset=dataset, grounding_helper=other_helper)
    lesson_graph = build_lesson_graph(content_graph, exercise_graph)
    output = await lesson_graph.ainvoke({"skill_label": "SQL joins", "user_id": "alice", "public_query": "Learn SQL", "primary_language": "sql"})
    helper.assert_called_once_with("SQL joins", "alice", public_query="Learn SQL")
    other_helper.assert_not_called()
    assert practice.synthesize.call_args.kwargs["knowledge_evidence"] == bundle()
    assert practice.synthesize.call_args.kwargs["lesson_content"] == output["content"]
    github.assert_not_called()
    blog.gather_facts.assert_not_called()
    dataset.gather_facts.assert_not_called()


def test_reopen_after_new_upload_preserves_authored_bundle_and_revision(monkeypatch):
    output = {"content": "Saved lesson", "grounding": bundle(), "exercise_format": "case_study",
              "exercise_statement": "Saved practice", "grading_artifact": {"rubric": []}}
    compose = Mock(return_value=(output, {"tokens_in": 0, "tokens_out": 0, "est_cost_usd": 0, "unknown": False}))
    monkeypatch.setattr(service, "_run_lesson", compose)
    state = service.new_learning_session({"skill_graph": deepcopy(GRAPH), "public_query": "Learn SQL"}, "B")
    service.open_lesson(state, "joins", "alice")
    saved = deepcopy(state["lessons"]["joins"])
    output["grounding"] = Evidence(status="unavailable").model_dump()  # later evidence changes
    service.open_lesson(state, "joins", "alice")
    assert state["lessons"]["joins"] == saved and compose.call_count == 1


def test_tutor_filters_split_invented_citations_and_saves_structured_sources(monkeypatch):
    from api.main import app
    import api.routes as routes
    from persistence.repository import get_default_repository
    repo = get_default_repository()
    state = service.new_learning_session({"skill_graph": deepcopy(GRAPH), "public_query": "Learn SQL"}, "B")
    repo.save("grounding-chat", "session", state)
    helper = Mock(return_value=bundle())
    monkeypatch.setattr(routes, "retrieve_evidence", helper)

    class Tutor:
        def build_messages(self, **kwargs):
            return []

        async def astream(self, messages):
            for token in ["Yes [sou", "rce:p_local] and [source:", "invented", "]."]:
                yield token

    monkeypatch.setattr(service, "_get_tutor_agent", lambda: Tutor())
    response = TestClient(app).post("/sessions/grounding-chat/session/chat", json={"message": "PRIVATE question", "node_id": "joins"})
    assert response.status_code == 200 and "[source:invented]" not in response.text
    assert "unsupported source" in response.text and '"grounding"' in response.text
    helper.assert_called_once_with("SQL joins: Join rows\nQuestion: PRIVATE question", "grounding-chat", public_query="Learn SQL")
    turn = repo.get("grounding-chat", "session")["chat"][-1]
    assert turn["grounding"]["status"] == "insufficient"
    assert turn["content"] == "Yes [source:p_local] and [unsupported source]."
