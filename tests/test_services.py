"""Service-layer orchestration (Streamlit-free): session construction, lesson open + lock gate, and
grade → mastery → adapt. LLM/graph calls are monkeypatched so these stay fast, deterministic unit
tests. The mastery/completion math itself is covered by test_mastery_capture / test_completion_gating."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

import pytest

from services import learning_service as svc


@pytest.fixture(autouse=True)
def no_live_assessment(monkeypatch):
    monkeypatch.setattr(svc, "_run_assessment", lambda *args, **kwargs: None)


def _usage(tokens_in=100, tokens_out=50, cost=0.0001):
    """Canned TokenMeter.totals() for stubbed _run_lesson mocks (now returns (out, usage))."""
    return {"tokens_in": tokens_in, "tokens_out": tokens_out, "est_cost_usd": cost,
            "by_model": {}, "unknown": False}


def _scout_state():
    return {
        "route": "SCOUT",
        "skill_graph": {
            "summary": "Path",
            "nodes": [
                {"id": "a", "label": "A", "description": "first", "level": "foundational"},
                {"id": "b", "label": "B", "description": "second", "level": "intermediate"},
            ],
            "edges": [{"source": "a", "target": "b", "relation": "prerequisite"}],
        },
        "format_type": "B",
    }


def test_new_learning_session_builds_available_node_state():
    ls = svc.new_learning_session(_scout_state(), "B")
    assert ls is not None
    assert set(ls["node_state"]) == {"a", "b"}
    assert all(s["status"] == "available" for s in ls["node_state"].values())
    assert ls["lessons"] == {}
    assert ls["selected_node"] is None


def test_new_learning_session_none_without_nodes():
    assert svc.new_learning_session({"skill_graph": {"nodes": []}}, "B") is None
    assert svc.new_learning_session({}, "B") is None


def test_open_lesson_blocks_locked_node(monkeypatch):
    monkeypatch.setattr(svc, "_run_lesson", lambda *a, **k: ({"content": "x"}, _usage()))
    ls = svc.new_learning_session(_scout_state(), "B")
    # 'b' depends on 'a'; 'a' not mastered ⇒ 'b' locked.
    with pytest.raises(svc.LockedNodeError) as ei:
        svc.open_lesson(ls, "b")
    assert "A" in ei.value.pending


def test_open_lesson_composes_and_caches(monkeypatch):
    calls = {"n": 0}

    def fake_run_lesson(node, fmt, weak, user_id=None, path_context=None):
        calls["n"] += 1
        return {
            "content": f"lesson for {node['id']}",
            "exercise_format": "coding_challenge",
            "exercise_statement": "do it",
            "grading_artifact": {"format": "coding_challenge", "unit_tests": []},
        }, _usage(tokens_in=120, tokens_out=60, cost=0.0002)

    monkeypatch.setattr(svc, "_run_lesson", fake_run_lesson)
    ls = svc.new_learning_session(_scout_state(), "B")
    svc.open_lesson(ls, "a")  # root node, unlocked
    assert ls["selected_node"] == "a"
    assert ls["lessons"]["a"]["content"] == "lesson for a"
    # Miss records per-lesson + session usage.
    assert ls["lessons"]["a"]["usage"]["tokens_in"] == 120
    assert ls["usage"]["composes"] == 1
    assert ls["usage"]["tokens_in"] == 120 and ls["usage"]["tokens_out"] == 60
    assert ls["usage"]["est_cost_usd"] == 0.0002
    svc.open_lesson(ls, "a")  # re-open reuses cache, no second compose
    assert calls["n"] == 1
    # Hit increments cache_hits, leaves compose count + cost untouched.
    assert ls["usage"]["cache_hits"] == 1
    assert ls["usage"]["composes"] == 1
    assert ls["usage"]["est_cost_usd"] == 0.0002


def test_grade_applies_mastery_and_runs_adaptation(monkeypatch):
    monkeypatch.setattr(svc, "_run_lesson", lambda *a, **k: ({
        "content": "c", "exercise_format": "coding_challenge",
        "exercise_statement": "s", "grading_artifact": {"format": "coding_challenge", "unit_tests": []},
    }, _usage()))
    # Grade returns a perfect score; adaptation is stubbed (cover the wiring, not the LLM).
    import agents.exercise.grader_agent as grader
    monkeypatch.setattr(grader, "grade_submission", lambda fmt, sol, art: {"score": 100, "passed": 1, "total": 1})
    adapt_calls = {"n": 0}
    monkeypatch.setattr(svc, "adapt_after_grade", lambda ls, nid: adapt_calls.__setitem__("n", adapt_calls["n"] + 1))

    ls = svc.new_learning_session(_scout_state(), "B")
    svc.open_lesson(ls, "a")
    svc.grade(ls, "a", "solution code", exercise_revision=ls.get("lessons", {}).get("a", {}).get("exercise", {}).get("revision"))
    assert ls["node_state"]["a"]["status"] == "mastered"
    assert ls["node_state"]["a"]["best_score"] == 100
    assert adapt_calls["n"] == 1


def test_grade_without_open_lesson_raises():
    ls = svc.new_learning_session(_scout_state(), "B")
    with pytest.raises(ValueError):
        svc.grade(ls, "a", "code", exercise_revision=ls.get("lessons", {}).get("a", {}).get("exercise", {}).get("revision"))


# --- threshold gating + deterministic remediation lock -----------------------------------------

class _FakeAgent:
    """Records adapt() calls and returns a canned result."""
    def __init__(self, result=None):
        self.result = result
        self.calls = 0

    def adapt(self, *args, **kwargs):
        self.calls += 1
        return self.result


def _opened(monkeypatch, score):
    """Build a session with node 'a' opened and grading stubbed to a fixed score."""
    monkeypatch.setattr(svc, "_run_lesson", lambda *a, **k: ({
        "content": "c", "exercise_format": "coding_challenge",
        "exercise_statement": "s", "grading_artifact": {"format": "coding_challenge", "unit_tests": []},
    }, _usage()))
    import agents.exercise.grader_agent as grader
    monkeypatch.setattr(grader, "grade_submission", lambda fmt, sol, art: {"score": score})
    ls = svc.new_learning_session(_scout_state(), "B")
    svc.open_lesson(ls, "a")
    return ls


def test_grade_40_79_skips_adaptation(monkeypatch):
    agent = _FakeAgent(result=None)
    monkeypatch.setattr(svc, "_get_adaptation_agent", lambda: agent)
    ls = _opened(monkeypatch, 55)
    svc.grade(ls, "a", "sol", exercise_revision=ls.get("lessons", {}).get("a", {}).get("exercise", {}).get("revision"))
    assert ls["node_state"]["a"]["status"] == "in_progress"
    assert agent.calls == 0  # 40–79 band makes NO adaptation call
    assert ls["node_state"]["a"]["remediation_pending"] is False


def test_grade_sub40_locks_even_when_adapt_returns_none(monkeypatch):
    from services.completion import locked_node_ids

    agent = _FakeAgent(result=None)  # LLM "fails" — no remedial nodes
    monkeypatch.setattr(svc, "_get_adaptation_agent", lambda: agent)
    ls = _opened(monkeypatch, 20)
    svc.grade(ls, "a", "sol", exercise_revision=ls.get("lessons", {}).get("a", {}).get("exercise", {}).get("revision"))
    assert ls["node_state"]["a"]["status"] == "needs_review"
    assert ls["node_state"]["a"]["remediation_pending"] is True
    assert agent.calls >= 1  # attempted (with bounded retry)
    assert "a" in locked_node_ids(ls["skill_graph"], ls["node_state"])  # locked despite None


def test_grade_sub40_adds_remedial_prereq_and_locks(monkeypatch):
    from agents.adaptation.adaptation_schema import GraphAdaptation
    from agents.consensus.skill_graph_schema import SkillEdge, SkillNode
    from services.completion import locked_node_ids

    adaptation = GraphAdaptation(
        new_nodes=[SkillNode(id="a_basics", label="A Basics", description="foundations", level="foundational")],
        new_edges=[SkillEdge(source="a_basics", target="a", relation="prerequisite")],
        remediation_focus=["foundations"],
        rationale="break it down",
    )
    monkeypatch.setattr(svc, "_get_adaptation_agent", lambda: _FakeAgent(result=adaptation))
    ls = _opened(monkeypatch, 20)
    svc.grade(ls, "a", "sol", exercise_revision=ls.get("lessons", {}).get("a", {}).get("exercise", {}).get("revision"))
    assert "a_basics" in ls["node_state"]            # remedial prereq seeded
    assert "a" in locked_node_ids(ls["skill_graph"], ls["node_state"])  # locked behind new prereq
    assert "a" not in ls["lessons"]                  # cached lesson invalidated for regeneration


def test_grade_sub40_orphaned_remedial_chain_is_still_completable(monkeypatch):
    """The live qwen2.5:7b regression: the LLM chains remedial nodes only to EACH OTHER
    (setup_environment -> verify_setup) with NO edge into the graded node. Without the deterministic
    synthesis in graph/skill_graph_adapt.py this leaves the graded node locked forever (prereqs_by_node
    is empty, so `satisfied` in services/completion.py:_remediation_locked can never become True). This
    test proves the dead end is closed: the graded node gets a real prerequisite path, and completing
    the remedial nodes actually unlocks it."""
    from agents.adaptation.adaptation_schema import GraphAdaptation
    from agents.consensus.skill_graph_schema import SkillEdge, SkillNode
    from services.completion import locked_node_ids

    orphaned_chain = GraphAdaptation(
        new_nodes=[
            SkillNode(id="setup_environment", label="Setup Env", description="x", level="foundational"),
            SkillNode(id="verify_setup", label="Verify Setup", description="x", level="foundational"),
        ],
        new_edges=[SkillEdge(source="setup_environment", target="verify_setup", relation="prerequisite")],
        remediation_focus=["environment setup"],
        rationale="break it down",
    )
    monkeypatch.setattr(svc, "_get_adaptation_agent", lambda: _FakeAgent(result=orphaned_chain))
    ls = _opened(monkeypatch, 20)
    svc.grade(ls, "a", "sol", exercise_revision=ls.get("lessons", {}).get("a", {}).get("exercise", {}).get("revision"))

    # Both remedial nodes landed, but the graph as proposed still doesn't reach the graded node — the
    # synthesis must have added the missing edge from the end of the chain.
    assert {"setup_environment", "verify_setup"} <= set(ls["node_state"])
    edges = ls["skill_graph"]["edges"]
    assert any(e["source"] == "verify_setup" and e["target"] == "a" for e in edges)
    assert "a" in locked_node_ids(ls["skill_graph"], ls["node_state"])  # still locked: prereqs incomplete

    # Complete the remedial chain -> the graded node must unlock (the dead end is gone).
    for nid in ("setup_environment", "verify_setup"):
        ls["node_state"][nid]["status"] = "mastered"
        ls["node_state"][nid]["best_score"] = 100
    assert "a" not in locked_node_ids(ls["skill_graph"], ls["node_state"])


def test_cached_lesson_and_usage_survive_persist_reload(monkeypatch):
    """The cache + cost accounting live in the JSONB session blob, so a save→reload round-trip (the
    real cross-restart durability path) must return both intact. json.dumps in the repo also proves
    the usage dict is JSONB-serializable."""
    from persistence.repository import InMemoryLearningSessionRepository

    monkeypatch.setattr(svc, "_run_lesson", lambda *a, **k: (
        {"content": "lesson a", "exercise_format": "coding_challenge",
         "exercise_statement": "s", "grading_artifact": {"format": "coding_challenge", "unit_tests": []}},
        _usage(tokens_in=200, tokens_out=80, cost=0.0003),
    ))
    ls = svc.new_learning_session(_scout_state(), "B")
    svc.open_lesson(ls, "a")

    repo = InMemoryLearningSessionRepository()
    repo.save("u1", "s1", ls)
    reloaded = repo.get("u1", "s1")

    assert reloaded["lessons"]["a"]["content"] == "lesson a"
    assert reloaded["lessons"]["a"]["usage"]["tokens_in"] == 200
    # Same cost-key name at both blob levels (per-lesson and session): est_cost_usd.
    assert reloaded["lessons"]["a"]["usage"]["est_cost_usd"] == 0.0003
    assert reloaded["usage"] == {
        "composes": 1, "cache_hits": 0, "tokens_in": 200, "tokens_out": 80, "est_cost_usd": 0.0003,
    }
    # Funnel events ride in the same JSONB blob — survive the round-trip too.
    stages = [e["stage"] for e in reloaded["events"]]
    assert "session_created" in stages and "lesson_opened" in stages


# --- funnel instrumentation (Gate-1) -----------------------------------------------------------

def _stages(ls):
    return [e["stage"] for e in ls.get("events", [])]


def test_open_lesson_emits_lesson_opened_events(monkeypatch):
    monkeypatch.setattr(svc, "_run_lesson", lambda *a, **k: ({"content": "x"}, _usage()))
    ls = svc.new_learning_session(_scout_state(), "B")
    assert _stages(ls) == ["session_created"]
    svc.open_lesson(ls, "a")
    svc.open_lesson(ls, "a")  # re-open = cache hit
    opened = [e for e in ls["events"] if e["stage"] == "lesson_opened"]
    assert [e["cache_hit"] for e in opened] == [False, True]


def test_grade_emits_exercise_graded_and_mastered(monkeypatch):
    monkeypatch.setattr(svc, "_run_lesson", lambda *a, **k: ({
        "content": "c", "exercise_format": "coding_challenge",
        "exercise_statement": "s", "grading_artifact": {"format": "coding_challenge", "unit_tests": []},
    }, _usage()))
    import agents.exercise.grader_agent as grader
    monkeypatch.setattr(grader, "grade_submission", lambda fmt, sol, art: {"score": 100, "passed": 1})
    monkeypatch.setattr(svc, "adapt_after_grade", lambda ls, nid: None)  # isolate event wiring
    ls = svc.new_learning_session(_scout_state(), "B")
    svc.open_lesson(ls, "a")
    svc.grade(ls, "a", "sol", exercise_revision=ls.get("lessons", {}).get("a", {}).get("exercise", {}).get("revision"))
    assert "exercise_graded" in _stages(ls)
    assert "node_mastered" in _stages(ls)
    graded = next(e for e in ls["events"] if e["stage"] == "exercise_graded")
    assert graded["score"] == 100.0 and graded["node_id"] == "a"


def test_subforty_grade_emits_needs_review_and_regenerated(monkeypatch):
    from agents.adaptation.adaptation_schema import GraphAdaptation
    from agents.consensus.skill_graph_schema import SkillEdge, SkillNode

    adaptation = GraphAdaptation(
        new_nodes=[SkillNode(id="a_basics", label="A Basics", description="x", level="foundational")],
        new_edges=[SkillEdge(source="a_basics", target="a", relation="prerequisite")],
        remediation_focus=["foundations"],
        rationale="break it down",
    )
    monkeypatch.setattr(svc, "_get_adaptation_agent", lambda: _FakeAgent(result=adaptation))
    ls = _opened(monkeypatch, 20)
    svc.grade(ls, "a", "sol", exercise_revision=ls.get("lessons", {}).get("a", {}).get("exercise", {}).get("revision"))
    assert "node_needs_review" in _stages(ls)
    assert "lesson_regenerated" in _stages(ls)


def test_path_completed_fires_once(monkeypatch):
    """Single-node path: mastering the only node completes the path; re-grade must not duplicate."""
    one_node = {
        "route": "SCOUT",
        "skill_graph": {"summary": "P", "nodes": [
            {"id": "a", "label": "A", "description": "d", "level": "foundational"}], "edges": []},
        "format_type": "B",
    }
    monkeypatch.setattr(svc, "_run_lesson", lambda *a, **k: ({
        "content": "c", "exercise_format": "coding_challenge",
        "exercise_statement": "s", "grading_artifact": {"format": "coding_challenge", "unit_tests": []},
    }, _usage()))
    import agents.exercise.grader_agent as grader
    monkeypatch.setattr(grader, "grade_submission", lambda fmt, sol, art: {"score": 100, "passed": 1})
    monkeypatch.setattr(svc, "adapt_after_grade", lambda ls, nid: None)
    ls = svc.new_learning_session(one_node, "B")
    svc.open_lesson(ls, "a")
    svc.grade(ls, "a", "sol", exercise_revision=ls.get("lessons", {}).get("a", {}).get("exercise", {}).get("revision"))
    svc.grade(ls, "a", "sol again", exercise_revision=ls.get("lessons", {}).get("a", {}).get("exercise", {}).get("revision"))
    assert _stages(ls).count("path_completed") == 1


def test_is_session_complete():
    from services.completion import is_session_complete

    ls = svc.new_learning_session(_scout_state(), "B")  # 2 nodes a→b, none mastered
    assert is_session_complete(ls["skill_graph"], ls["node_state"]) is False
    for nid in ("a", "b"):
        ls["node_state"][nid]["status"] = "mastered"
        ls["node_state"][nid]["best_score"] = 100
    assert is_session_complete(ls["skill_graph"], ls["node_state"]) is True
