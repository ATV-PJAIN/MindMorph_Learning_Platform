"""Regression coverage for the remaining progress and onboarding defects."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from api.main import app
from api import routes
from graph.validation import validate_skill_graph
from graph.skill_graph_adapt import apply_adaptation
from persistence.errors import SessionConflictError, StorageUnavailableError
from persistence.repository import InMemoryLearningSessionRepository, PostgresLearningSessionRepository
from services import learning_service as svc
from services.learner_profile import make_profile
from services.mastery import default_node_state


def session():
    return {"skill_graph": {"summary": "Path", "nodes": [{"id": "a"}, {"id": "b"}], "edges": []},
            "node_state": {"a": default_node_state(), "b": default_node_state()}, "events": [],
            "lessons": {"a": {"exercise": {"revision": "v1", "format": "coding_challenge", "grading_artifact": {}}}}}


@pytest.mark.parametrize("method,args", [
    ("get", ("u", "s")), ("save", ("u", "s", {})), ("list_sessions", ("u",)),
    ("delete", ("u", "s")), ("list_all", ()),
])
def test_database_errors_never_use_memory(monkeypatch, method, args):
    from persistence import repository, db
    memory = Mock()
    monkeypatch.setattr(repository, "_memory_singleton", memory)
    monkeypatch.setattr(db, "get_sessionmaker", Mock(side_effect=OperationalError("query", {}, Exception("secret"))))
    with pytest.raises(StorageUnavailableError):
        getattr(PostgresLearningSessionRepository(), method)(*args)
    assert not memory.mock_calls


def test_storage_and_conflict_errors_are_safe_http_responses(monkeypatch):
    repo = Mock()
    monkeypatch.setattr(routes, "get_default_repository", lambda: repo)
    repo.get.side_effect = StorageUnavailableError("secret connection string")
    response = TestClient(app).get("/sessions/u/s")
    assert response.status_code == 503 and "secret" not in response.text
    repo.get.side_effect = None
    repo.get.return_value = session()
    repo.save.side_effect = SessionConflictError("Reload the path before trying again.")
    response = TestClient(app).post("/sessions/u/s/lessons/a/flag", json={})
    assert response.status_code == 409 and "Reload" in response.text


def test_readiness_distinguishes_running_from_database_ready(monkeypatch):
    from persistence import db
    monkeypatch.setenv("MINDMORPH_STORE", "postgres")
    monkeypatch.setattr(db, "check_ready", Mock(side_effect=RuntimeError("secret")))
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503
    assert "secret" not in client.get("/ready").text
    monkeypatch.setattr(db, "check_ready", lambda: None)
    assert client.get("/ready").json()["durable"] is True
    monkeypatch.setenv("MINDMORPH_STORE", "memory")
    assert client.get("/ready").json()["durable"] is False


def test_two_simultaneous_saves_cannot_overwrite_one_another():
    repo = InMemoryLearningSessionRepository()
    repo.save("u", "s", {"grade": 0, "chat": []})
    barrier = Barrier(2)
    def change(field, value):
        snapshot = repo.get("u", "s")
        snapshot[field] = value
        barrier.wait(timeout=5)
        try:
            repo.save("u", "s", snapshot)
            return "saved"
        except SessionConflictError:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(change, "grade", 100), pool.submit(change, "chat", ["hello"])]
        assert sorted(j.result() for j in jobs) == ["conflict", "saved"]
    # An explicit reload + retry preserves both fields, without repeating a grading side effect.
    snapshot = repo.get("u", "s")
    snapshot.update(grade=100, chat=["hello"])
    repo.save("u", "s", snapshot)
    assert repo.get("u", "s") == {"grade": 100, "chat": ["hello"]}
    with pytest.raises(SessionConflictError):
        repo.save("u", "s", {"grade": -1})  # no unversioned overwrite loophole


def test_quiz_retries_preserve_mastery_and_result_identifiers():
    ls = session()
    ls["node_state"]["a"].update(status="mastered", best_score=100, attempts=3)
    ls["assessment"] = {"submitted": False, "quiz": {"questions": [
        {"node_id": "a", "options": ["yes", "no"], "correct_index": 0},
        {"node_id": "a", "options": ["one", "two"], "correct_index": 1},
    ]}}
    svc.grade_assessment(ls, [0, 1])
    assert ls["node_state"]["a"]["status"] == "mastered"
    assert ls["node_state"]["a"]["attempts"] == 3
    assert [r["question_id"] for r in ls["assessment"]["results"]] == ["q1", "q2"]
    before = copy.deepcopy(ls)
    svc.grade_assessment(ls, [-1, -1])
    assert ls == before


def test_invalid_quiz_answer_is_rejected_before_any_progress_changes():
    ls = session()
    ls["assessment"] = {"quiz": {"questions": [{"node_id": "a", "options": ["x", "y"], "correct_index": 0}]}}
    before = copy.deepcopy(ls)
    with pytest.raises(ValueError):
        svc.grade_assessment(ls, [2])
    assert ls == before


@pytest.mark.parametrize("bad", [
    {"nodes": [{"id": "a"}, {"id": "a"}], "edges": []},
    {"nodes": [{"id": "a"}], "edges": [{"source": "a", "target": "a"}]},
    {"nodes": [{"id": "a"}, {"id": "b"}], "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}]},
    {"nodes": [{"id": "a", "level": "impossible"}], "edges": []},
])
def test_invalid_roadmaps_are_rejected(bad):
    with pytest.raises(ValueError):
        validate_skill_graph(bad)


def test_adaptation_cycle_does_not_mutate_original_graph():
    graph = session()["skill_graph"]
    graph["edges"] = [{"source": "a", "target": "b"}]
    before = copy.deepcopy(graph)
    with pytest.raises(ValueError):
        apply_adaptation(graph, {"new_edges": [{"source": "b", "target": "a"}]})
    assert graph == before


def test_grade_rejects_stale_missing_and_locked_exercises_before_execution(monkeypatch):
    grader = Mock()
    monkeypatch.setattr("agents.exercise.grader_agent.grade_submission", grader)
    ls = session()
    for revision in (None, "old"):
        with pytest.raises(svc.StaleExerciseError):
            svc.grade(ls, "a", "answer", exercise_revision=revision)
    ls["skill_graph"]["edges"] = [{"source": "b", "target": "a"}]
    with pytest.raises(svc.LockedNodeError):
        svc.grade(ls, "a", "answer", exercise_revision="v1")
    grader.assert_not_called()
    assert ls["node_state"]["a"]["attempts"] == 0


def test_selected_language_and_scope_override_detected_defaults():
    profile = make_profile({"language": "js", "scope": "Focused (only my subject)", "experience": "beginner", "goal": "build a portfolio"}, "python", "foundation", "Programming")
    assert profile["primary_language"] == "javascript" and profile["scope"] == "focused"
    assert profile["experience"] == "beginner" and profile["goal"] == "build a portfolio"
    from api.schemas import CreateSessionRequest
    with pytest.raises(ValueError):
        CreateSessionRequest(user_id="u", query="learn", scope="unrecognized")


async def test_non_coding_practice_avoids_coding_sources_and_fallback():
    from graph.exercise_graph import build_exercise_graph
    selector, synth, grader, blog, dataset = (Mock() for _ in range(5))
    synth.synthesize.return_value = "Play a chord progression"
    grader.build_grading_artifact.return_value = None
    github = AsyncMock()
    state = await build_exercise_graph(selector, synth, grader, github, blog, dataset).ainvoke({
        "user_query": "Guitar chords", "learner_profile": make_profile({"experience": "beginner"}, domain="Music")})
    assert state["exercise_format"] == "case_study"
    selector.select_format.assert_not_called()
    github.assert_not_called()
    dataset.gather_facts.assert_not_called()
    assert "beginner" in synth.synthesize.call_args.args[0]


async def test_profile_reaches_lesson_and_example_generation(monkeypatch):
    from graph.lesson_graph import build_lesson_graph
    from graph.content_graph import build_content_graph
    profile = make_profile({"experience": "beginner", "goal": "build a website"}, "js", "focused", "Programming")
    content, exercise = AsyncMock(), AsyncMock()
    content.ainvoke.return_value = {"final_content": "lesson"}
    exercise.ainvoke.return_value = {}
    await build_lesson_graph(content, exercise).ainvoke({"skill_label": "Functions", "learner_profile": profile})
    assert content.ainvoke.call_args.args[0]["learner_profile"] == profile
    assert exercise.ainvoke.call_args.args[0]["learner_profile"] == profile
    monkeypatch.setenv("MINDMORPH_RICH_CONTENT", "1")
    creative, factual, synth, example, visual = (Mock() for _ in range(5))
    creative.generate_content.return_value = "draft"
    factual.gather_facts.return_value = None
    synth.synthesize.return_value = "lesson"
    example.generate_example.return_value = None
    visual.generate_diagram.return_value = None
    await build_content_graph(creative, factual, synth, example=example, visual=visual).ainvoke({
        "user_query": "Functions", "primary_language": "javascript", "learner_profile": profile})
    assert "beginner" in creative.generate_content.call_args.args[0]
    assert "javascript" in example.generate_example.call_args.args[2]
    assert "build a website" in synth.synthesize.call_args.args[0]


def test_chat_finishing_after_a_grade_reports_conflict_and_preserves_grade(monkeypatch):
    repo = InMemoryLearningSessionRepository()
    repo.save("u", "s", {**session(), "chat": []})
    monkeypatch.setattr(routes, "get_default_repository", lambda: repo)
    monkeypatch.setattr(routes, "build_tutor_messages", lambda *a, **kwargs: [])
    class Tutor:
        async def astream(self, _messages):
            newer = repo.get("u", "s")
            newer["node_state"]["a"].update(status="mastered", best_score=100)
            repo.save("u", "s", newer)
            yield "A reply"
    monkeypatch.setattr(svc, "_get_tutor_agent", lambda: Tutor())
    response = TestClient(app).post("/sessions/u/s/chat", json={"message": "Help"})
    frames = [json.loads(part.removeprefix("data: ")) for part in response.text.strip().split("\n\n")]
    assert "error" in frames[-1] and "done" not in frames[-1]
    saved = repo.get("u", "s")
    assert saved["node_state"]["a"]["status"] == "mastered"
    assert saved["chat"] == [{"role": "user", "content": "Help"}]


def test_followup_schema_rejects_empty_duplicate_and_bad_default_questions():
    from agents.followup.followup_schema import FollowUpQuestions
    question = {"id": "scope", "question": "Depth?", "options": ["focused", "foundation"], "default": 0}
    for questions in ([], [question, question], [{**question, "default": 5}], [{**question, "options": []}]):
        with pytest.raises(ValueError):
            FollowUpQuestions(questions=questions, detected_domain="Programming", reasoning="test")


async def test_planning_uses_preferences_and_avoids_technical_sources_for_music(monkeypatch):
    import graph.learning_plan_graph as plan
    orchestrator, scout, academic, market, practical, consensus, reviewer, factual = (Mock() for _ in range(8))
    orchestrator.route_query.return_value = SimpleNamespace(Assigned_Agent="SCOUT", Reasoning="test")
    scout.generate_specialized_queries.return_value = {"sub_agent_queries": {"ACADEMIC": "Music curriculum", "PRACTICAL": "Guitar practice"}}
    academic.provide_academic_roadmap.return_value = SimpleNamespace(content="Chords")
    factual.gather_facts.return_value = "Instrument exercises"
    consensus.build_skill_graph.return_value = session()["skill_graph"]
    reviewer.review_skill_graph.return_value = SimpleNamespace(passed=True, notes="ok")
    github = AsyncMock()
    monkeypatch.setattr(plan, "_fetch_github_repos", github)
    profile = make_profile({"experience": "beginner", "goal": "perform a song"}, scope="foundation", domain="Music")
    await plan.build_graph(orchestrator, scout, academic, market, practical, consensus, reviewer, factual=factual).ainvoke({
        "user_query": "Learn guitar", "learner_profile": profile, "scope": "foundation"})
    for call in (scout.generate_specialized_queries, academic.provide_academic_roadmap, consensus.build_skill_graph):
        assert "beginner" in call.call_args.args[0] and "perform a song" in call.call_args.args[0]
    assert "universal CS" not in consensus.build_skill_graph.call_args.kwargs["scope_instruction"]
    market.extract_job_title.assert_not_called()
    github.assert_not_called()
    factual.gather_facts.assert_called_once()


async def test_invalid_initial_graph_has_one_repair_attempt(monkeypatch):
    import graph.learning_plan_graph as plan
    orchestrator, scout, academic, market, practical, consensus, reviewer = (Mock() for _ in range(7))
    orchestrator.route_query.return_value = SimpleNamespace(Assigned_Agent="SCOUT", Reasoning="test")
    scout.generate_specialized_queries.return_value = {}
    academic.provide_academic_roadmap.return_value = None
    practical.provide_practical_advice.return_value = None
    monkeypatch.setattr(plan, "_run_market", AsyncMock(return_value=None))
    monkeypatch.setattr(plan, "_fetch_github_repos", AsyncMock(return_value=None))
    invalid = {"nodes": [{"id": "a"}], "edges": [{"source": "a", "target": "a"}]}
    consensus.build_skill_graph.side_effect = [invalid, session()["skill_graph"]]
    reviewer.review_skill_graph.return_value = SimpleNamespace(passed=True, notes="ok")
    graph = plan.build_graph(orchestrator, scout, academic, market, practical, consensus, reviewer)
    result = await graph.ainvoke({"user_query": "Learn"})
    assert result["skill_graph"] == session()["skill_graph"]
    assert consensus.build_skill_graph.call_count == 2
    consensus.build_skill_graph.side_effect = [invalid, invalid]
    with pytest.raises(ValueError):
        await graph.ainvoke({"user_query": "Learn"})
