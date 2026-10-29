"""Routing + fan-in tests for the top-level MindMorph graph, all agents mocked
(no real LLM / network)."""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

import graph.learning_plan_graph as glp
from graph.learning_plan_graph import build_graph


def _mock_scout():
    scout = MagicMock()
    out = MagicMock()
    out.model_dump.return_value = {
        "sub_agent_queries": {"ACADEMIC": "a", "MARKET": "m", "PRACTICAL": "p"}
    }
    scout.generate_specialized_queries.return_value = out
    return scout


def _mock_market():
    market = MagicMock()
    market.extract_job_title = AsyncMock(return_value="ML Engineer")
    market.scraper.initialize = AsyncMock()
    market.scraper.search_jobs = AsyncMock(return_value="dataset123")
    market.scraper.fetch_job_results = AsyncMock(
        return_value=[{"title": "ML Engineer", "organization": "Acme"}]
    )
    market.summarize_job = AsyncMock(return_value="JOB_SUMMARY")
    return market


def _mock_consensus():
    consensus = MagicMock()
    sg = MagicMock()
    sg.model_dump.return_value = {
        "summary": "roadmap",
        "nodes": [{"id": "python_basics", "label": "Python Basics", "level": "foundational"}],
        "edges": [],
    }
    consensus.build_skill_graph.return_value = sg
    return consensus


def _mock_reviewer():
    reviewer = MagicMock()
    reviewer.review_skill_graph.return_value = MagicMock(passed=True, notes="Looks coherent.")
    return reviewer


async def test_scout_route_runs_full_learning_plan(monkeypatch):
    monkeypatch.setattr(glp, "_fetch_github_repos", AsyncMock(return_value=None))

    orchestrator = MagicMock()
    orchestrator.route_query.return_value = MagicMock(Assigned_Agent="SCOUT", Reasoning="r")
    academic = MagicMock()
    academic.provide_academic_roadmap.return_value = MagicMock(content="ACADEMIC_RESULT")
    practical = MagicMock()
    practical.provide_practical_advice.return_value = MagicMock(content="PRACTICAL_RESULT")
    consensus = _mock_consensus()
    reviewer = _mock_reviewer()

    graph = build_graph(
        orchestrator=orchestrator,
        scout=_mock_scout(),
        academic=academic,
        market=_mock_market(),
        practical=practical,
        consensus=consensus,
        reviewer=reviewer,
    )
    state = await graph.ainvoke({"user_query": "learn ML"})

    assert state["route"] == "SCOUT"
    assert state["academic_output"] == "ACADEMIC_RESULT"
    assert state["market_output"]["summary"] == "JOB_SUMMARY"
    assert state["practical_output"] == "PRACTICAL_RESULT"
    # Consensus fan-in: runs exactly ONCE after all three specialists (LangGraph superstep),
    # not once per incoming edge.
    assert consensus.build_skill_graph.call_count == 1
    # Consensus saw all three perspectives.
    consensus.build_skill_graph.assert_called_once_with(
        "learn ML", "ACADEMIC_RESULT", "JOB_SUMMARY", "PRACTICAL_RESULT", scope_instruction=""
    )
    # Reviewer runs after consensus, exactly once.
    assert reviewer.review_skill_graph.call_count == 1
    assert state["skill_graph"]["summary"] == "roadmap"
    assert "flowchart TD" in state["skill_graph_mermaid"]
    assert state["review_passed"] is True
    assert state["review_notes"] == "Looks coherent."


async def test_content_route_runs_dual_path(monkeypatch):
    # Routing/dual-path test asserts the synthesizer output verbatim → run with the §6.3 tail OFF
    # (the tail is covered in test_content_tail.py). build_graph builds the content sub-graph
    # internally with no tail injection, so without this it would fire the real example/visual agents.
    monkeypatch.setenv("MINDMORPH_RICH_CONTENT", "0")
    orchestrator = MagicMock()
    orchestrator.route_query.return_value = MagicMock(Assigned_Agent="CONTENT", Reasoning="r")

    content = MagicMock()
    content.generate_content.return_value = "CREATIVE_DRAFT"
    factual = MagicMock()
    factual.gather_facts.return_value = "FACTUAL_FINDINGS"
    synthesizer = MagicMock()
    synthesizer.synthesize.return_value = "FINAL_LESSON"

    graph = build_graph(
        orchestrator=orchestrator,
        content=content,
        factual=factual,
        synthesizer=synthesizer,
    )
    state = await graph.ainvoke({"user_query": "Python lists", "format_type": "B"})

    assert state["route"] == "CONTENT"
    assert state["creative_draft"] == "CREATIVE_DRAFT"
    assert state["factual_findings"] == "FACTUAL_FINDINGS"
    assert state["final_content"] == "FINAL_LESSON"
    content.generate_content.assert_called_once_with("Python lists", "B", remediation=None, context=None, primary_language=None)
    synthesizer.synthesize.assert_called_once_with("Python lists", "CREATIVE_DRAFT", "FACTUAL_FINDINGS")


async def test_exercise_route_runs_exercise_subgraph(monkeypatch):
    # Neutralize the exercise sub-graph's live I/O: GitHub MCP + the FactualAgent (ddgs) blog/dataset
    # sources default to real network calls, which tests must not make.
    monkeypatch.setattr(glp, "_fetch_github_repos", AsyncMock(return_value=None))
    from agents.factual.factual_agent import FactualAgent
    monkeypatch.setattr(FactualAgent, "gather_facts", lambda self, query: None)

    orchestrator = MagicMock()
    orchestrator.route_query.return_value = MagicMock(Assigned_Agent="EXERCISE", Reasoning="r")
    scout = MagicMock()
    consensus = MagicMock()

    format_selector = MagicMock()
    format_selector.select_format.return_value = MagicMock(format="coding_challenge", reasoning="r", language="python")
    exercise_synthesizer = MagicMock()
    exercise_synthesizer.synthesize.return_value = "EXERCISE_STATEMENT"
    grader = MagicMock()
    artifact = MagicMock()
    artifact.model_dump.return_value = {
        "format": "coding_challenge",
        "unit_tests": ["def test_x(): assert True"],
        "rubric": [],
        "instructions": "submit",
    }
    grader.build_grading_artifact.return_value = artifact

    graph = build_graph(
        orchestrator=orchestrator,
        scout=scout,
        consensus=consensus,
        format_selector=format_selector,
        exercise_synthesizer=exercise_synthesizer,
        grader=grader,
    )
    state = await graph.ainvoke({"user_query": "give me practice"})

    assert state["route"] == "EXERCISE"
    assert state["exercise_format"] == "coding_challenge"
    assert state["exercise_statement"] == "EXERCISE_STATEMENT"
    assert state["grading_artifact"]["unit_tests"]
    scout.generate_specialized_queries.assert_not_called()
    consensus.build_skill_graph.assert_not_called()
    assert "skill_graph" not in state


async def test_astream_session_emits_stage_per_node_in_graph_order(monkeypatch):
    """astream_session streams over the SAME compiled graph as start_session (no separate graph
    construction) — proves stage frames arrive in real topological order and the terminal frame's
    learning_session matches what ainvoke would have produced."""
    import services.learning_service as svc

    monkeypatch.setattr(svc, "_get_assessment_agent", lambda: type(
        "NoAssessment", (), {"assess": lambda self, sg_json: None}
    )())
    monkeypatch.setattr(glp, "_fetch_github_repos", AsyncMock(return_value=None))

    orchestrator = MagicMock()
    orchestrator.route_query.return_value = MagicMock(Assigned_Agent="SCOUT", Reasoning="r")
    academic = MagicMock()
    academic.provide_academic_roadmap.return_value = MagicMock(content="ACADEMIC_RESULT")
    practical = MagicMock()
    practical.provide_practical_advice.return_value = MagicMock(content="PRACTICAL_RESULT")
    consensus = _mock_consensus()
    reviewer = _mock_reviewer()

    graph = build_graph(
        orchestrator=orchestrator,
        scout=_mock_scout(),
        academic=academic,
        market=_mock_market(),
        practical=practical,
        consensus=consensus,
        reviewer=reviewer,
    )
    monkeypatch.setattr(svc, "_get_orchestration_graph", lambda: graph)

    events = [e async for e in svc.astream_session("learn ML")]

    stage_events = [e for e in events if "stage" in e]
    stages = [e["stage"] for e in stage_events]
    # orchestrator first, reviewer last; the three specialists fan out from scout (any relative order
    # among themselves, since LangGraph doesn't guarantee sibling ordering within a superstep) then
    # fan in to consensus.
    assert stages[0] == "orchestrator"
    assert stages[1] == "scout"
    assert set(stages[2:6]) == {"academic", "market", "practical", "knowledge"}
    assert stages[6:] == ["consensus", "reviewer"]
    assert all(e["label"] for e in stage_events)  # every stage carries a human-readable label

    terminal = events[-1]
    assert "result" in terminal
    result = terminal["result"]
    assert result["route"] == "SCOUT"
    ls = result["learning_session"]
    assert ls is not None
    assert ls["skill_graph"]["summary"] == "roadmap"
    assert ls["academic_output"] == "ACADEMIC_RESULT"


async def test_astream_session_content_route_short_circuits_like_start_session(monkeypatch):
    """The important subtlety from the brief: CONTENT (and EXERCISE) routes short-circuit past the
    specialist pipeline in start_session's post-loop routing — astream_session must route the SAME
    way via the shared `_route_final_state` helper, not duplicate/diverge on it."""
    import services.learning_service as svc

    monkeypatch.setenv("MINDMORPH_RICH_CONTENT", "0")
    orchestrator = MagicMock()
    orchestrator.route_query.return_value = MagicMock(Assigned_Agent="CONTENT", Reasoning="r")

    content = MagicMock()
    content.generate_content.return_value = "CREATIVE_DRAFT"
    factual = MagicMock()
    factual.gather_facts.return_value = "FACTUAL_FINDINGS"
    synthesizer = MagicMock()
    synthesizer.synthesize.return_value = "FINAL_LESSON"

    graph = build_graph(
        orchestrator=orchestrator,
        content=content,
        factual=factual,
        synthesizer=synthesizer,
    )
    monkeypatch.setattr(svc, "_get_orchestration_graph", lambda: graph)

    events = [e async for e in svc.astream_session("Python lists", "B")]

    terminal = events[-1]
    result = terminal["result"]
    assert result["route"] == "CONTENT"
    assert result["final_content"] == "FINAL_LESSON"
    assert result["learning_session"] is None  # CONTENT never persists a learning_session
