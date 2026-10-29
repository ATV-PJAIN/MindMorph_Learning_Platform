"""In-situ exercise of four fixes whose failure branches a full integration run never reached.

Each fix has unit coverage already; what was missing is evidence the path behaves as its commit
claims when driven through the REAL graph / REAL API rather than in isolation. Every scenario
triggers the genuine failure condition at the outermost stub-able boundary (the MCP transport, the
Apify tool list, the persisted session blob) and asserts the observable behaviour — the WARNING and
its content, and that the run still produces a usable artifact instead of an error.

Hermetic: no network, no LLM. Nothing here relaxes a production guard.

  f75378f  GitHub grounding failure logging
  3e48f5f  MARKET-lost WARNING
  e9694bb  read-time phantom-prerequisite guard
"""
import asyncio
import json
import logging
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

os.environ["MINDMORPH_STORE"] = "memory"  # before api import

import graph.learning_plan_graph as glp
import tools.github_mcp_client as ghc
from graph.learning_plan_graph import build_graph
from services.completion import (
    _warn_dangling,
    is_session_complete,
    locked_node_ids,
    prereqs_by_node,
)
from tools.job_scrapper_tool import JobScraperService


# --- shared mocks (mirroring tests/test_learning_plan_graph.py) -----------------------------------

def _mock_scout():
    scout = MagicMock()
    out = MagicMock()
    out.model_dump.return_value = {
        "sub_agent_queries": {"ACADEMIC": "a", "MARKET": "m", "PRACTICAL": "p"}
    }
    scout.generate_specialized_queries.return_value = out
    return scout


def _healthy_market():
    market = MagicMock()
    market.extract_job_title = AsyncMock(return_value="ML Engineer")
    market.scraper.initialize = AsyncMock()
    market.scraper.search_jobs = AsyncMock(return_value="dataset123")
    market.scraper.fetch_job_results = AsyncMock(return_value=[{"title": "ML Engineer"}])
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


def _graph(*, market=None, practical=None, consensus=None):
    orchestrator = MagicMock()
    orchestrator.route_query.return_value = MagicMock(Assigned_Agent="SCOUT", Reasoning="r")
    academic = MagicMock()
    academic.provide_academic_roadmap.return_value = MagicMock(content="ACADEMIC_RESULT")
    if practical is None:
        practical = MagicMock()
        practical.provide_practical_advice.return_value = MagicMock(content="PRACTICAL_RESULT")
    reviewer = MagicMock()
    reviewer.review_skill_graph.return_value = MagicMock(passed=True, notes="ok")
    return build_graph(
        orchestrator=orchestrator,
        scout=_mock_scout(),
        academic=academic,
        market=market or _healthy_market(),
        practical=practical,
        consensus=consensus or _mock_consensus(),
        reviewer=reviewer,
    )


class _FakeMCPTransport:
    """Stands in for MultiServerMCPClient. ``get_tools`` either raises (unreachable host) or
    returns a tool list whose search result we control."""

    def __init__(self, *, raises=None, tools=None):
        self._raises = raises
        self._tools = tools or []

    async def get_tools(self):
        if self._raises:
            raise self._raises
        return self._tools


class _SearchTool:
    name = "search_repositories"
    description = "search repositories"  # initialize() logs these at DEBUG (args evaluated eagerly)
    args = {}

    def __init__(self, result):
        self._result = result
        self.calls = []

    async def ainvoke(self, args):
        self.calls.append(args)
        return self._result


# =================================================================================================
# f75378f — GitHub grounding failure logging, driven through the real Practical node
# =================================================================================================

@pytest.mark.asyncio
async def test_unreachable_github_logs_the_reason_and_still_advises(monkeypatch, caplog):
    """Transport-level failure (unreachable endpoint). The real MCPClientInitialization and the real
    _fetch_github_repos run; only the HTTP client is swapped. Advice must still be produced."""
    monkeypatch.setenv("GITHUB_PERSONAL_TOKEN", "token-present")
    monkeypatch.setattr(
        ghc, "MultiServerMCPClient",
        lambda *a, **k: _FakeMCPTransport(raises=ConnectionError("Cannot connect to host api.githubcopilot.com")),
    )
    practical = MagicMock()
    practical.provide_practical_advice.return_value = MagicMock(content="PRACTICAL_RESULT")

    with caplog.at_level(logging.WARNING):
        state = await _graph(practical=practical).ainvoke({"user_query": "learn rust"})

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("GitHub grounding failed" in r.getMessage() for r in errors), caplog.text
    # The reason is named, not just "something failed".
    assert "Cannot connect to host" in caplog.text
    # grounded=False still yields usable advice: the agent was called with github_repos=None and
    # the graph completed.
    assert practical.provide_practical_advice.call_args.kwargs["github_repos"] is None
    assert state["practical_output"] == "PRACTICAL_RESULT"
    assert state["skill_graph"]["summary"] == "roadmap"


@pytest.mark.asyncio
async def test_github_returning_no_repositories_logs_empty_grounding(monkeypatch, caplog):
    """The 'legitimately returns nothing' case — the one that a term-capped keyword query could
    turn into a quiet grounded=True."""
    monkeypatch.setenv("GITHUB_PERSONAL_TOKEN", "token-present")
    tool = _SearchTool(result=None)
    monkeypatch.setattr(ghc, "MultiServerMCPClient", lambda *a, **k: _FakeMCPTransport(tools=[tool]))
    practical = MagicMock()
    practical.provide_practical_advice.return_value = MagicMock(content="PRACTICAL_RESULT")

    with caplog.at_level(logging.WARNING):
        state = await _graph(practical=practical).ainvoke({"user_query": "learn rust"})

    assert any(
        "GitHub grounding empty" in r.getMessage()
        for r in caplog.records if r.levelno == logging.WARNING
    ), caplog.text
    assert tool.calls, "the search tool was never invoked"
    assert practical.provide_practical_advice.call_args.kwargs["github_repos"] is None
    assert state["practical_output"] == "PRACTICAL_RESULT"


@pytest.mark.asyncio
async def test_missing_token_in_situ_names_the_env_var(monkeypatch, caplog):
    monkeypatch.setenv("GITHUB_PERSONAL_TOKEN", "")
    practical = MagicMock()
    practical.provide_practical_advice.return_value = MagicMock(content="PRACTICAL_RESULT")

    with caplog.at_level(logging.WARNING):
        state = await _graph(practical=practical).ainvoke({"user_query": "learn rust"})

    assert any(
        "GITHUB_PERSONAL_TOKEN is not set" in r.getMessage() for r in caplog.records
    ), caplog.text
    assert state["practical_output"] == "PRACTICAL_RESULT"


@pytest.mark.asyncio
async def test_exercise_graph_grounding_failure_is_reported_without_blocking_generation(monkeypatch, caplog):
    """A failed shared evidence source stays visible without blocking valid practice generation."""
    monkeypatch.setenv("GITHUB_PERSONAL_TOKEN", "")
    monkeypatch.setattr("rag.registry.get_user_store", MagicMock(side_effect=RuntimeError("unavailable")))
    from graph.exercise_graph import build_exercise_graph

    format_selector = MagicMock()
    format_selector.select_format.return_value = MagicMock(format="coding_challenge", reasoning="r", language="python")
    synthesizer = MagicMock()
    synthesizer.synthesize.return_value = "EXERCISE_STATEMENT"
    grader = MagicMock()
    artifact = MagicMock()
    artifact.model_dump.return_value = {"format": "coding_challenge", "unit_tests": [],
                                        "rubric": [], "instructions": "go"}
    grader.build_grading_artifact.return_value = artifact
    from agents.factual.factual_agent import FactualAgent
    monkeypatch.setattr(FactualAgent, "gather_facts", lambda self, query: None)

    g = build_exercise_graph(format_selector=format_selector, synthesizer=synthesizer, grader=grader)
    with caplog.at_level(logging.WARNING):
        state = await g.ainvoke({"user_query": "practice rust", "user_id": "exercise-user"})

    assert state["grounding"]["status"] == "unavailable"
    assert "personal_unavailable" in state["grounding"]["warnings"]
    assert state["exercise_statement"] == "EXERCISE_STATEMENT"


# =================================================================================================
# 3e48f5f — MARKET lost: loud degradation, Consensus proceeds from Academic + Practical
# =================================================================================================

@pytest.mark.asyncio
async def test_zero_job_items_warns_loudly_and_consensus_proceeds(monkeypatch, caplog):
    """The observed live failure: the scrape succeeds but returns zero items."""
    monkeypatch.setattr(glp, "_fetch_github_repos", AsyncMock(return_value=None))
    market = _healthy_market()
    market.scraper.fetch_job_results = AsyncMock(return_value=[])
    consensus = _mock_consensus()

    with caplog.at_level(logging.WARNING):
        state = await _graph(market=market, consensus=consensus).ainvoke({"user_query": "learn ML"})

    degraded = [r for r in caplog.records if "Consensus degraded" in r.getMessage()]
    assert degraded, caplog.text
    msg = degraded[0].getMessage()
    assert degraded[0].levelno == logging.WARNING
    assert "'learn ML'" in msg                                   # names the query
    assert "Academic + Practical only" in msg                    # names what it was built from
    # Consensus still ran, from two of three perspectives, and the session is intact.
    consensus.build_skill_graph.assert_called_once_with(
        "learn ML", "ACADEMIC_RESULT", None, "PRACTICAL_RESULT", scope_instruction=""
    )
    assert state["market_output"] is None
    assert state["skill_graph"]["summary"] == "roadmap"
    assert "flowchart TD" in state["skill_graph_mermaid"]


@pytest.mark.asyncio
async def test_market_timeout_warns_the_same_way(monkeypatch, caplog):
    monkeypatch.setattr(glp, "_fetch_github_repos", AsyncMock(return_value=None))
    market = _healthy_market()
    market.scraper.search_jobs = AsyncMock(side_effect=asyncio.TimeoutError())
    consensus = _mock_consensus()

    with caplog.at_level(logging.WARNING):
        state = await _graph(market=market, consensus=consensus).ainvoke({"user_query": "learn ML"})

    assert any("Consensus degraded" in r.getMessage() for r in caplog.records), caplog.text
    assert state["market_output"] is None
    assert state["skill_graph"]["summary"] == "roadmap"


@pytest.mark.asyncio
async def test_a_wedged_scraper_client_does_not_survive_the_failure(caplog):
    """'every failure path clears it' — a cached tool list that blew up must not persist into the
    next request."""
    svc = JobScraperService()
    boom = MagicMock()
    boom.name = JobScraperService.SEARCH_TOOL_NAME
    boom.ainvoke = AsyncMock(side_effect=RuntimeError("actor exploded"))
    svc.client = object()
    svc.tools = [boom]

    with caplog.at_level(logging.WARNING):
        assert await svc.search_jobs("ML Engineer") is None

    assert svc.tools == [] and svc.client is None
    assert "client reset" in caplog.text


# =================================================================================================
# e9694bb — read-time phantom-prerequisite guard, through the real API
# =================================================================================================

PHANTOM = "mlops_tools_phantom_insitu"  # unique so the per-shape warn cache is cold for this test


def _broken_session() -> dict:
    """A session as it would have been persisted BEFORE 84f4db7: an edge whose SOURCE is a node id
    the graph never contained. b can never be mastered while that gate stands."""
    return {
        "skill_graph": {
            "summary": "Path",
            "nodes": [
                {"id": "a", "label": "A", "description": "first", "level": "foundational"},
                {"id": "b", "label": "B", "description": "second", "level": "intermediate"},
            ],
            "edges": [
                {"source": "a", "target": "b", "relation": "prerequisite"},
                {"source": PHANTOM, "target": "b", "relation": "prerequisite"},
            ],
        },
        "node_state": {
            "a": {"status": "available", "best_score": 0, "attempts": 0, "weaknesses": [],
                  "last_feedback": None},
            "b": {"status": "available", "best_score": 0, "attempts": 0, "weaknesses": [],
                  "last_feedback": None},
        },
        "lessons": {},
        "selected_node": None,
        "format_type": "B",
    }


@pytest.fixture
def api_client(monkeypatch):
    import api.routes as routes
    from fastapi.testclient import TestClient
    from api.main import app
    from persistence.repository import _memory_singleton
    from services.learning_service import LockedNodeError

    _memory_singleton._store.clear()

    def fake_open(ls, node_id, user_id=None):
        # Real gate, stubbed lesson compose (the LLM boundary).
        if node_id in locked_node_ids(ls["skill_graph"], ls["node_state"]):
            raise LockedNodeError(node_id, ["A"])
        ls["selected_node"] = node_id
        ls["lessons"][node_id] = {"content": "lesson", "exercise": {
            "format": "coding_challenge", "statement": "s", "grading_artifact": {}}}
        return ls

    def fake_grade(ls, node_id, solution):
        ls["node_state"][node_id]["status"] = "mastered"
        ls["node_state"][node_id]["best_score"] = 100
        return ls, [], {"score": 100}

    monkeypatch.setattr(routes, "open_lesson", fake_open)
    monkeypatch.setattr(routes, "grade", fake_grade)
    yield TestClient(app)
    _memory_singleton._store.clear()


def test_phantom_source_session_is_finishable_through_the_api(api_client, caplog):
    from persistence.repository import _memory_singleton, get_default_repository

    repo = get_default_repository()
    repo.save("u1", "s1", _broken_session(), title="rescue me")
    stored_before = _memory_singleton._store[("u1", "s1")]["data"]
    _warn_dangling.cache_clear()

    with caplog.at_level(logging.WARNING):
        assert api_client.get("/sessions/u1/s1").status_code == 200
        # a is a root, b's only real prerequisite. Both open (b would be 409 with the phantom gate).
        for node in ("a", "b"):
            r = api_client.post(f"/sessions/u1/s1/lessons/{node}")
            assert r.status_code == 200, (node, r.text)
            r = api_client.post(f"/sessions/u1/s1/grade?node_id={node}",
                                json={"solution": "print(1)"})
            assert r.status_code == 200, (node, r.text)

    final = api_client.get("/sessions/u1/s1").json()["learning_session"]
    assert is_session_complete(final["skill_graph"], final["node_state"]) is True

    # Deduped per shape: prereqs_by_node runs many times across those requests.
    warnings = [r for r in caplog.records
                if "referencing unknown node ids" in r.getMessage() and PHANTOM in r.getMessage()]
    assert len(warnings) == 1, [w.getMessage() for w in warnings]

    # Nothing stored was rewritten: the phantom edge is still there, byte-identical edge list.
    assert json.loads(stored_before)["skill_graph"]["edges"] == final["skill_graph"]["edges"]
    assert any(e["source"] == PHANTOM for e in final["skill_graph"]["edges"])


def test_remediation_pending_node_with_a_phantom_remedial_edge_can_retry(monkeypatch):
    """The second permanent-lock state e9694bb names: the phantom source made the prerequisite set
    truthy, so learning_service's retry condition never fired."""
    import services.learning_service as svc

    ls = _broken_session()
    ls["skill_graph"]["edges"] = [{"source": PHANTOM, "target": "b", "relation": "prerequisite"}]
    ls["node_state"]["b"]["remediation_pending"] = True
    ls["node_state"]["b"]["status"] = "needs_review"

    # The gate still holds (remediation_pending with no satisfied prereqs) ...
    assert "b" in locked_node_ids(ls["skill_graph"], ls["node_state"])
    # ... but the prerequisite set is now empty, which is what arms the retry.
    assert prereqs_by_node(ls["skill_graph"])["b"] == set()

    attempted = []
    monkeypatch.setattr(svc, "adapt_after_grade", lambda _ls, node_id: attempted.append(node_id))
    with pytest.raises(svc.LockedNodeError):
        svc.open_lesson(ls, "b")
    assert attempted == ["b"], "adapt_after_grade was never re-attempted on open"
