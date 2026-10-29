"""FastAPI integration — full loop over HTTP against the in-memory store, with the LLM/graph calls
monkeypatched. Proves: create persists a session, get/list read it back, lesson + grade mutate and
persist, the lock gate returns 409, and unknown ids return 404."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

os.environ["MINDMORPH_STORE"] = "memory"  # zero-infra store for the test (set before app import)

import pytest
from fastapi.testclient import TestClient

import api.routes as routes
from api.main import app
from persistence.repository import _memory_singleton
from services.learning_service import LockedNodeError


def _scout_ls():
    return {
        "skill_graph": {
            "summary": "Path",
            "nodes": [
                {"id": "a", "label": "A", "description": "first", "level": "foundational"},
                {"id": "b", "label": "B", "description": "second", "level": "intermediate"},
            ],
            "edges": [{"source": "a", "target": "b", "relation": "prerequisite"}],
        },
        "node_state": {
            "a": {"status": "available", "best_score": 0, "attempts": 0, "weaknesses": [], "last_feedback": None},
            "b": {"status": "available", "best_score": 0, "attempts": 0, "weaknesses": [], "last_feedback": None},
        },
        "lessons": {},
        "selected_node": None,
        "format_type": "B",
    }


@pytest.fixture(autouse=True)
def _clean_store():
    _memory_singleton._store.clear()
    yield
    _memory_singleton._store.clear()


@pytest.fixture
def client(monkeypatch):
    # start_session returns a SCOUT result with a fresh learning_session (no orchestration graph).
    monkeypatch.setattr(
        routes, "start_session",
        lambda query, fmt="B", **kwargs: {"route": "SCOUT", "learning_session": _scout_ls(),
                            "final_content": None, "exercise": None},
    )

    def fake_open(ls, node_id, user_id=None):
        from services.completion import locked_node_ids
        if node_id in locked_node_ids(ls["skill_graph"], ls["node_state"]):
            raise LockedNodeError(node_id, ["A"])
        ls["selected_node"] = node_id
        ls["lessons"][node_id] = {"content": "lesson", "exercise": {"format": "coding_challenge",
                                  "statement": "s", "grading_artifact": {}}}
        return ls

    def fake_grade(ls, node_id, solution):
        ls["node_state"][node_id]["status"] = "mastered"
        ls["node_state"][node_id]["best_score"] = 100
        return ls, [], {"score": 100}

    monkeypatch.setattr(routes, "open_lesson", fake_open)
    monkeypatch.setattr(routes, "grade", fake_grade)
    return TestClient(app)


def test_health_reports_memory_store(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["store"] == "memory"


def test_create_session_persists_and_returns_graph(client):
    r = client.post("/sessions", json={"user_id": "u1", "query": "learn python"})
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "SCOUT"
    sid = body["session_id"]
    assert sid and len(body["learning_session"]["node_state"]) == 2
    # Persisted: a fresh GET returns the same session.
    assert client.get(f"/sessions/u1/{sid}").json()["learning_session"]["node_state"]["a"]["status"] == "available"


def _read_sse_frames(response) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in response.iter_lines()
        if line.startswith("data: ")
    ]


def test_create_session_stream_emits_stages_then_terminal_frame(client, monkeypatch):
    """The streaming create route must emit one frame per LangGraph node (in order) before the
    terminal frame — the whole point being live progress instead of one static ~60s wait."""

    async def fake_astream_session(query, fmt, **kwargs):
        yield {"stage": "scout", "label": "Scout planning specialist queries"}
        yield {"stage": "academic", "label": "Academic agent researching"}
        yield {"stage": "consensus", "label": "Building consensus skill graph"}
        yield {"stage": "reviewer", "label": "Reviewing the skill graph"}
        yield {"result": {"route": "SCOUT", "learning_session": _scout_ls(),
                           "final_content": None, "exercise": None}}

    monkeypatch.setattr(routes, "astream_session", fake_astream_session)

    with client.stream(
        "POST", "/sessions/stream", json={"user_id": "u1", "query": "learn python"}
    ) as r:
        assert r.status_code == 200
        frames = _read_sse_frames(r)

    stage_frames = [f for f in frames if "stage" in f]
    assert [f["stage"] for f in stage_frames] == ["scout", "academic", "consensus", "reviewer"]
    assert all("label" in f for f in stage_frames)

    terminal = frames[-1]
    assert terminal["done"] is True
    session = terminal["session"]
    assert session["route"] == "SCOUT"
    assert session["session_id"]
    assert session["learning_session"]["node_state"]["a"]["status"] == "available"

    # The terminal frame's session_id was actually persisted (same contract as the sync route).
    persisted = client.get(f"/sessions/u1/{session['session_id']}")
    assert persisted.status_code == 200


def test_create_session_stream_surfaces_mid_stream_failure_as_error_frame(client, monkeypatch):
    """A mid-stream LLM/graph failure must arrive as an explicit error frame, never a stream that
    just stops with the client's spinner running forever."""

    async def fake_astream_session(query, fmt, **kwargs):
        yield {"stage": "scout", "label": "Scout planning specialist queries"}
        raise RuntimeError("llm provider 500: internal boom")

    monkeypatch.setattr(routes, "astream_session", fake_astream_session)

    with client.stream(
        "POST", "/sessions/stream", json={"user_id": "u1", "query": "learn python"}
    ) as r:
        assert r.status_code == 200
        frames = _read_sse_frames(r)

    assert frames[0] == {"stage": "scout", "label": "Scout planning specialist queries"}
    assert "error" in frames[-1]
    assert "internal boom" not in frames[-1]["error"]  # safe message only, no leak


def test_list_sessions_shows_created(client):
    client.post("/sessions", json={"user_id": "u1", "query": "first path"})
    metas = client.get("/sessions/u1").json()
    assert len(metas) == 1 and metas[0]["title"] == "first path"


def test_get_missing_session_404(client):
    assert client.get("/sessions/u1/nope").status_code == 404


def test_open_lesson_then_grade_persists_mastery(client):
    sid = client.post("/sessions", json={"user_id": "u1", "query": "p"}).json()["session_id"]
    # 'a' is the unlocked root.
    r = client.post(f"/sessions/u1/{sid}/lessons/a")
    assert r.status_code == 200 and r.json()["learning_session"]["selected_node"] == "a"
    g = client.post(f"/sessions/u1/{sid}/grade", params={"node_id": "a"}, json={"solution": "code"})
    assert g.status_code == 200
    # Re-fetch: mastery durably persisted.
    after = client.get(f"/sessions/u1/{sid}").json()["learning_session"]
    assert after["node_state"]["a"]["status"] == "mastered"


def test_locked_lesson_returns_409(client):
    sid = client.post("/sessions", json={"user_id": "u1", "query": "p"}).json()["session_id"]
    r = client.post(f"/sessions/u1/{sid}/lessons/b")  # 'b' locked behind 'a'
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "locked"


def test_lesson_generation_failure_returns_503_not_500(client, monkeypatch):
    # An LLM/generation failure (e.g. a provider rate limit) must surface as a graceful 503 with a safe
    # message, never a raw 500 that leaks internals.
    sid = client.post("/sessions", json={"user_id": "u1", "query": "p"}).json()["session_id"]

    def boom(ls, node_id):
        raise RuntimeError("llm provider 413 rate_limit_exceeded: super secret internals")

    monkeypatch.setattr(routes, "open_lesson", boom)
    r = client.post(f"/sessions/u1/{sid}/lessons/a")
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert isinstance(detail, str) and "try again" in detail.lower()
    assert "secret" not in detail  # internal error text not leaked


def test_compose_failure_records_event_before_503(client, monkeypatch):
    sid = client.post("/sessions", json={"user_id": "u1", "query": "p"}).json()["session_id"]
    monkeypatch.setattr(routes, "open_lesson", lambda ls, node_id, user_id=None: (_ for _ in ()).throw(RuntimeError("boom")))
    assert client.post(f"/sessions/u1/{sid}/lessons/a").status_code == 503
    # The failed attempt was persisted as a funnel signal.
    after = client.get(f"/sessions/u1/{sid}").json()["learning_session"]
    assert any(e["stage"] == "compose_failure" for e in after.get("events", []))


def test_flag_endpoint_records_content_flagged(client):
    sid = client.post("/sessions", json={"user_id": "u1", "query": "p"}).json()["session_id"]
    r = client.post(f"/sessions/u1/{sid}/lessons/a/flag", json={"reason": "wrong language"})
    assert r.status_code == 200
    flagged = [e for e in r.json()["learning_session"]["events"] if e["stage"] == "content_flagged"]
    assert len(flagged) == 1 and flagged[0]["node_id"] == "a" and flagged[0]["reason"] == "wrong language"


def _seed(user_id, session_id, events):
    _memory_singleton.save(user_id, session_id, {"events": events})


def test_admin_funnel_requires_token(client, monkeypatch):
    monkeypatch.setenv("MINDMORPH_ADMIN_TOKEN", "s3cret")
    assert client.get("/admin/funnel").status_code == 403  # no header
    assert client.get("/admin/funnel", headers={"x-admin-token": "wrong"}).status_code == 403


def test_admin_funnel_disabled_without_env(client, monkeypatch):
    monkeypatch.delenv("MINDMORPH_ADMIN_TOKEN", raising=False)
    assert client.get("/admin/funnel", headers={"x-admin-token": "x"}).status_code == 503


def test_grade_sub40_adds_remedial_node_and_locks_via_http(monkeypatch):
    """The demo's central beat, exercised over HTTP: wrong solution -> score <40 -> a remedial
    prerequisite node is added to the graph -> its id comes back in `new_node_ids` -> re-opening the
    graded node is now locked (409). Only the LLM boundary (adaptation call + grader) is stubbed; the
    real grade -> adapt_after_grade -> apply_adaptation -> locked_node_ids chain runs through the route."""
    import services.learning_service as svc
    from agents.adaptation.adaptation_schema import GraphAdaptation
    from agents.consensus.skill_graph_schema import SkillEdge, SkillNode

    monkeypatch.setattr(
        routes, "start_session",
        lambda query, fmt="B", **kwargs: {"route": "SCOUT", "learning_session": _scout_ls(),
                            "final_content": None, "exercise": None},
    )
    # Stub the lesson-compose LLM boundary only; open_lesson/grade themselves run for real.
    monkeypatch.setattr(svc, "_run_lesson", lambda *a, **k: ({
        "content": "c", "exercise_format": "coding_challenge",
        "exercise_statement": "s", "grading_artifact": {"format": "coding_challenge", "unit_tests": []},
    }, {"composes": 1, "cache_hits": 0, "tokens_in": 0, "tokens_out": 0, "est_cost_usd": 0.0, "unknown": True}))
    # Stub the grader LLM/execution boundary: force a sub-40 score.
    import agents.exercise.grader_agent as grader
    monkeypatch.setattr(grader, "grade_submission", lambda fmt, sol, art: {"score": 20, "passed": 0})
    # Stub the adaptation LLM boundary: propose one remedial prerequisite for node 'a'.
    adaptation = GraphAdaptation(
        new_nodes=[SkillNode(id="a_basics", label="A Basics", description="foundations", level="foundational")],
        new_edges=[SkillEdge(source="a_basics", target="a", relation="prerequisite")],
        remediation_focus=["foundations"],
        rationale="break it down",
    )

    class _FakeAgent:
        def adapt(self, *a, **k):
            return adaptation

    monkeypatch.setattr(svc, "_get_adaptation_agent", lambda: _FakeAgent())

    client = TestClient(app)
    sid = client.post("/sessions", json={"user_id": "u1", "query": "p"}).json()["session_id"]
    opened = client.post(f"/sessions/u1/{sid}/lessons/a")
    assert opened.status_code == 200

    g = client.post(f"/sessions/u1/{sid}/grade", params={"node_id": "a"}, json={"solution": "bad code", "exercise_revision": opened.json()["learning_session"]["lessons"]["a"]["exercise"]["revision"]})
    assert g.status_code == 200
    body = g.json()
    assert body["new_node_ids"] == ["a_basics"]
    node_ids = {n["id"] for n in body["learning_session"]["skill_graph"]["nodes"]}
    assert "a_basics" in node_ids

    # 'a' is now locked behind its fresh remedial prerequisite — the server-side gate must hold.
    r = client.post(f"/sessions/u1/{sid}/lessons/a")
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "locked"


def test_admin_funnel_aggregates(client, monkeypatch):
    monkeypatch.setenv("MINDMORPH_ADMIN_TOKEN", "s3cret")
    _seed("u1", "s1", [
        {"ts": "t", "stage": "session_created"},
        {"ts": "t", "stage": "lesson_opened", "node_id": "a", "cache_hit": False},
        {"ts": "t", "stage": "exercise_graded", "node_id": "a", "score": 90.0},
        {"ts": "t", "stage": "path_completed"},
    ])
    _seed("u2", "s2", [
        {"ts": "t", "stage": "session_created"},
        {"ts": "t", "stage": "content_flagged", "node_id": "a"},
    ])
    out = client.get("/admin/funnel", headers={"x-admin-token": "s3cret"}).json()
    assert out["total_sessions"] == 2
    assert out["reached_grade"] == 1
    assert out["completed"] == 1
    assert out["content_flags_total"] == 1
    assert out["sessions_reaching_stage"]["session_created"] == 2
