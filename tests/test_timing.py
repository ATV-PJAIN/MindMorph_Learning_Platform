"""Per-stage latency instrumentation (services/timing.py) — hermetic, all agents mocked."""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

import graph.learning_plan_graph as glp
import services.learning_service as svc
from services.timing import collect, span, spans_payload


# --- the span primitive ------------------------------------------------------------------------

def test_span_records_duration_and_start_offset():
    with collect() as spans:
        with span("a"):
            pass
        with span("b"):
            pass
    payload = spans_payload(spans)
    assert set(payload) == {"a", "b"}
    assert payload["a"]["ms"] >= 0.0
    # b started after a, so its offset from the collector's origin is at least a's.
    assert payload["b"]["start_ms"] >= payload["a"]["start_ms"]
    assert "_origin" not in payload  # private marker stripped before it reaches a payload


def test_span_outside_collector_is_log_only():
    with span("orphan"):  # must not raise without a collector in scope
        pass


def test_span_records_even_when_the_block_raises():
    with collect() as spans:
        with pytest.raises(RuntimeError):
            with span("boom"):
                raise RuntimeError("x")
    assert "boom" in spans


# --- graph stages ------------------------------------------------------------------------------

def _mock_agents(monkeypatch):
    monkeypatch.setattr(glp, "_fetch_github_repos", AsyncMock(return_value=None))
    orchestrator = MagicMock()
    orchestrator.route_query.return_value = MagicMock(Assigned_Agent="SCOUT", Reasoning="r")
    scout = MagicMock()
    queries = MagicMock()
    queries.model_dump.return_value = {
        "sub_agent_queries": {"ACADEMIC": "a", "MARKET": "m", "PRACTICAL": "p"}
    }
    scout.generate_specialized_queries.return_value = queries
    academic = MagicMock()
    academic.provide_academic_roadmap.return_value = MagicMock(content="ACADEMIC")
    market = MagicMock()
    market.extract_job_title = AsyncMock(return_value="ML Engineer")
    market.scraper.initialize = AsyncMock()
    market.scraper.search_jobs = AsyncMock(return_value="dataset123")
    market.scraper.fetch_job_results = AsyncMock(return_value=[{"title": "ML Engineer"}])
    market.summarize_job = AsyncMock(return_value="JOB_SUMMARY")
    practical = MagicMock()
    practical.provide_practical_advice.return_value = MagicMock(content="PRACTICAL")
    consensus = MagicMock()
    sg = MagicMock()
    sg.model_dump.return_value = {
        "summary": "roadmap",
        "nodes": [{"id": "n1", "label": "Node One", "level": "foundational"}],
        "edges": [],
    }
    consensus.build_skill_graph.return_value = sg
    reviewer = MagicMock()
    reviewer.review_skill_graph.return_value = MagicMock(passed=True, notes="ok")
    return dict(orchestrator=orchestrator, scout=scout, academic=academic, market=market,
                practical=practical, consensus=consensus, reviewer=reviewer)


GRAPH_STAGES = {
    "graph.orchestrator", "graph.scout", "graph.academic", "graph.market",
    "graph.practical", "graph.consensus", "graph.reviewer",
}


def test_every_graph_stage_is_timed_including_sync_nodes(monkeypatch):
    """The collector is a ContextVar, and five of the seven nodes are sync `def` (executor hop).
    This is the load-bearing assertion: if context didn't propagate, spans would silently vanish."""
    graph = glp.build_graph(**_mock_agents(monkeypatch))
    with collect() as spans:
        asyncio.run(graph.ainvoke({"user_query": "q", "format_type": "B"}))
    payload = spans_payload(spans)
    assert GRAPH_STAGES <= set(payload)
    assert all(v["ms"] >= 0.0 for v in payload.values())


def _patch_graph_singleton(monkeypatch, graph):
    monkeypatch.setattr(svc, "_orchestration_graph", graph)
    monkeypatch.setattr(svc, "_run_assessment", lambda sg: None)
    monkeypatch.setattr(svc, "_get_graph_from_cache", lambda c: None)
    monkeypatch.setattr(svc, "_save_graph_to_cache", lambda *a: None)


def test_start_session_attaches_graph_timing_to_the_session(monkeypatch):
    _patch_graph_singleton(monkeypatch, glp.build_graph(**_mock_agents(monkeypatch)))
    result = svc.start_session("learn python")
    timing = result["learning_session"]["timing"]["graph"]
    assert GRAPH_STAGES <= set(timing)
    assert "graph.total" in timing  # end-to-end graph construction


def test_astream_session_attaches_the_same_graph_timing(monkeypatch):
    """The streaming route is what the frontend actually calls — an async generator, where the
    ContextVar collector is easiest to get wrong."""
    _patch_graph_singleton(monkeypatch, glp.build_graph(**_mock_agents(monkeypatch)))

    async def run():
        final = None
        async for evt in svc.astream_session("learn python"):
            if "result" in evt:
                final = evt["result"]
        return final

    result = asyncio.run(run())
    timing = result["learning_session"]["timing"]["graph"]
    assert GRAPH_STAGES <= set(timing)
    assert "graph.total" in timing


def test_specialist_fan_out_overlaps(monkeypatch):
    """Start offsets (not just durations) — the fan-out only costs max() if the three truly overlap."""
    _patch_graph_singleton(monkeypatch, glp.build_graph(**_mock_agents(monkeypatch)))
    timing = svc.start_session("learn python")["learning_session"]["timing"]["graph"]
    starts = [timing[s]["start_ms"] for s in ("graph.academic", "graph.market", "graph.practical")]
    ends = [timing[s]["start_ms"] + timing[s]["ms"] for s in
            ("graph.academic", "graph.market", "graph.practical")]
    assert max(starts) < max(ends)  # a later specialist began before the last one finished


# --- lesson compose + grading --------------------------------------------------------------------

def _session_with_lesson():
    return {
        "skill_graph": {"nodes": [{"id": "n1", "label": "Node One", "description": "d"}], "edges": []},
        "node_state": {"n1": {"status": "available", "attempts": 0, "weaknesses": []}},
        "lessons": {},
        "events": [],
    }


def test_open_lesson_stores_compose_timing_beside_usage(monkeypatch):
    def fake_run_lesson(node, fmt, weak, user_id=None, path_context=None):
        # Stand in for the lesson graph's two nodes, which record these spans for real.
        with span("lesson.content"):
            pass
        with span("lesson.exercise"):
            pass
        return {"content": "c"}, {"tokens_in": 1, "tokens_out": 2, "est_cost_usd": 0.0,
                                  "by_model": {}, "unknown": False}

    monkeypatch.setattr(svc, "_run_lesson", fake_run_lesson)
    ls = svc.open_lesson(_session_with_lesson(), "n1")
    timing = ls["lessons"]["n1"]["timing"]
    assert {"lesson.total", "lesson.content", "lesson.exercise"} <= set(timing)
    assert "usage" in ls["lessons"]["n1"]  # sits beside the cost meter, same idiom


@pytest.mark.parametrize(
    "fmt,expected", [("coding_challenge", "grade.tests"), ("case_study", "grade.rubric_llm")]
)
def test_grade_timing_names_the_work_that_actually_ran(monkeypatch, fmt, expected):
    """coding_challenge runs pytest; case_study is an LLM rubric call — one name would misattribute."""
    import agents.exercise.grader_agent as grader

    monkeypatch.setattr(grader, "grade_submission",
                        lambda f, s, a: {"score": 90.0, "passed": True, "feedback": "ok"})
    monkeypatch.setattr(svc, "adapt_after_grade", lambda ls, node_id: [])
    ls = _session_with_lesson()
    ls["lessons"]["n1"] = {"content": "c", "exercise": {"format": fmt, "grading_artifact": {}}}
    ls, _, _ = svc.grade(ls, "n1", "solution")
    timing = ls["timing"]["grade"]
    assert expected in timing
    assert "grade.adapt" in timing  # test execution vs the adaptation LLM call, split


def test_grade_timing_stays_bounded_across_repeated_submissions(monkeypatch):
    import agents.exercise.grader_agent as grader

    monkeypatch.setattr(grader, "grade_submission",
                        lambda f, s, a: {"score": 90.0, "passed": True, "feedback": "ok"})
    monkeypatch.setattr(svc, "adapt_after_grade", lambda ls, node_id: [])
    ls = _session_with_lesson()
    ls["lessons"]["n1"] = {"content": "c",
                           "exercise": {"format": "coding_challenge", "grading_artifact": {}}}
    for _ in range(5):
        ls, _, _ = svc.grade(ls, "n1", "solution")
    assert set(ls["timing"]["grade"]) == {"grade.tests", "grade.adapt"}  # last grade only, never a list
