# Top-level MindMorph LangGraph state graph.
#
#   START -> orchestrator -> (route)
#     SCOUT    -> scout -> [academic | market | practical | knowledge] -> consensus -> reviewer -> END
#     CONTENT  -> content (dual-path content sub-graph) -> END
#     EXERCISE -> grounded exercise sub-graph -> END
#
# The four specialists explicitly join before Consensus. Consensus emits a
# Skill Dependency Graph (JSON), the renderer turns it into Mermaid, and Reviewer checks it.
# Specialist / content nodes write distinct state keys, so fan-out needs no custom reducer.

import asyncio
import logging
import sys
import os
import json
from typing import Any, Optional, TypedDict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

logger = logging.getLogger(__name__)

from langgraph.graph import StateGraph, START, END

from agents.orchestrator.orchestrator_agent import OrchestratorAgent
from agents.scout.scout_agent import ScoutAgent
from agents.academic.academic_agent import AcademicAgent
from agents.market.market_agent import MarketAnalysisAgent
from agents.practical.practical_agent import PracticalAgent
from agents.consensus.consensus_agent import ConsensusAgent
from agents.reviewer.reviewer_agent import ReviewerAgent
from agents.grounding.knowledge_grounding_agent import KnowledgeGroundingAgent
from rag.grounding import empty_evidence, validate_citations
from tools.github_mcp_client import MCPClientInitialization
from tools.multi_platform_job_scraper import MultiPlatformJobScraper
from graph.content_graph import build_content_graph
from graph.exercise_graph import build_exercise_graph
from graph.skill_graph_adapt import prune_dangling_edges
from graph.skill_graph_render import skill_graph_to_mermaid
from services.timing import span
from services.learner_profile import with_profile, is_technical
from graph.validation import validate_skill_graph


class LearningPlanState(TypedDict, total=False):
    """Shared state across both DAGs. Distinct keys per node => no custom reducer needed."""
    user_query: str
    format_type: str
    route: str
    reasoning: str
    learner_profile: Optional[dict]
    preferences: Optional[dict]
    primary_language: Optional[str]
    scope: Optional[str]
    user_id: Optional[str]
    graph_identity: Optional[str]
    public_query: Optional[str]
    grounding: Optional[dict]
    # Learning-plan branch
    scout_queries: dict
    academic_output: Optional[str]
    market_output: Optional[dict]
    practical_output: Optional[str]
    skill_graph: Optional[dict]
    skill_graph_mermaid: Optional[str]
    review_passed: Optional[bool]
    review_notes: Optional[str]
    # Content branch
    creative_draft: Optional[str]
    factual_findings: Optional[str]
    final_content: Optional[str]
    # Exercise branch
    exercise_format: Optional[str]
    exercise_statement: Optional[str]
    grading_artifact: Optional[dict]
    # Non-SCOUT/CONTENT/EXERCISE routes
    placeholder: Optional[str]


# --- helpers -------------------------------------------------------------------

def _normalize_scout_queries(scout_output: Any) -> dict:
    """Coerce the Scout's structured output into a plain {ACADEMIC, MARKET, PRACTICAL} dict."""
    if scout_output is None:
        return {}
    if hasattr(scout_output, "model_dump"):
        data = scout_output.model_dump()
    elif hasattr(scout_output, "dict"):
        data = scout_output.dict()
    elif isinstance(scout_output, dict):
        data = scout_output
    else:
        return {}
    return data.get("sub_agent_queries", {}) or {}


async def _run_market(market_agent: Any, query: str, location: str = "United States") -> Optional[dict]:
    """Port of app.py's run_market_analysis helper (the agent's own run_analysis only prints).

    The Scout MARKET query is a natural-language question; the job actor needs a concise
    role title, so we distill one before searching.
    """
    # Sub-spans: `graph.market` dominates the build, and the five sub-stages have very
    # different costs (two Apify round-trips, two LLM calls). Each is timed separately —
    # a single span around the gather below would report max(), not both legs.
    async def _init():
        with span("market.apify.initialize"):
            return await market_agent.scraper.initialize()

    async def _title():
        with span("market.llm.title"):
            return await market_agent.extract_job_title(query)

    try:
        # Independent: the MCP handshake doesn't need the title, the title doesn't need the client.
        _, job_title = await asyncio.gather(_init(), _title())

        jobs = []

        # 1. Apify LinkedIn
        dataset_id = None
        try:
            with span("market.apify.search"):
                dataset_id = await market_agent.scraper.search_jobs(job_title, location)
            if dataset_id:
                with span("market.apify.fetch"):
                    apify_jobs = await market_agent.scraper.fetch_job_results(dataset_id)
                    if apify_jobs and isinstance(apify_jobs, list):
                        for j in apify_jobs:
                            j.setdefault("platform", "LinkedIn")
                        jobs.extend(apify_jobs)
        except Exception as e:
            logger.warning("Market: Apify LinkedIn scrape unavailable (%s)", e)

        # 2. Multi-Platform open JDs (Indeed, Naukri, Glassdoor, Google search / open web)
        multi_scraper = getattr(market_agent, "multi_scraper", None)
        if isinstance(multi_scraper, MultiPlatformJobScraper):
            with span("market.multi_platform.fetch"):
                try:
                    open_jobs = await multi_scraper.fetch_open_job_postings(job_title, max_total=6)
                    if open_jobs and isinstance(open_jobs, list):
                        jobs.extend(open_jobs)
                except Exception as e:
                    logger.warning("Market: multi-platform open scrape error (%s)", e)

        if not jobs:
            return None

        with span("market.llm.summary"):
            # Sample across available postings from all platforms
            summary = await market_agent.summarize_job(jobs)
        # No raw posting here. services/learning_service.py ships this dict onto the wire and into
        # the persisted session, and the raw posting carries a named recruiter, their LinkedIn URL
        # and a contact email — a third party's personal data, stored and transmitted for a field
        # nothing reads (Consensus takes only `summary`; web/ never references market_output).
        return {"sampled": len(jobs), "summary": summary}
    except Exception:
        logger.exception("Market node error")
        return None


async def _fetch_github_repos(query: str) -> Optional[Any]:
    """Resilient GitHub MCP lookup. Returns None (no hard failure) when token/network is absent.

    Every ``None`` here becomes a silent ``grounded=False`` downstream, so each way of returning
    one says why at WARNING — otherwise ungrounded advice is indistinguishable from grounded.
    """
    try:
        client = MCPClientInitialization()
        if not client.token:
            logger.warning("GitHub grounding unavailable: GITHUB_PERSONAL_TOKEN is not set")
            return None
        await client.initialize()
        repos = await client.search_github_repositories(query)
        if not repos:
            logger.warning("GitHub grounding empty: no repositories returned for %r", query)
        return repos
    except Exception:
        logger.exception("GitHub grounding failed: repo fetch error")
        return None


# --- graph builder -------------------------------------------------------------

def build_graph(
    orchestrator: Optional[Any] = None,
    scout: Optional[Any] = None,
    academic: Optional[Any] = None,
    market: Optional[Any] = None,
    practical: Optional[Any] = None,
    consensus: Optional[Any] = None,
    reviewer: Optional[Any] = None,
    content: Optional[Any] = None,
    factual: Optional[Any] = None,
    synthesizer: Optional[Any] = None,
    format_selector: Optional[Any] = None,
    exercise_synthesizer: Optional[Any] = None,
    grader: Optional[Any] = None,
    grounding: Optional[Any] = None,
):
    """Build and compile the full MindMorph graph.

    All agents are injectable so tests can pass mocks; defaults are the real agents.
    The dual-path content sub-graph is built from (content, factual, synthesizer); the
    exercise sub-graph from (format_selector, exercise_synthesizer, grader).
    """
    orchestrator = orchestrator or OrchestratorAgent(push_to_langsmith=False)
    scout = scout or ScoutAgent(push_to_langsmith=False, output_variant="Query")
    academic = academic or AcademicAgent(push_to_langsmith=False)
    market = market or MarketAnalysisAgent()
    practical = practical or PracticalAgent(push_to_langsmith=False)
    consensus = consensus or ConsensusAgent(push_to_langsmith=False)
    reviewer = reviewer or ReviewerAgent(push_to_langsmith=False)
    grounding = grounding or KnowledgeGroundingAgent()
    content_graph = build_content_graph(content=content, factual=factual, synthesizer=synthesizer)
    exercise_graph = build_exercise_graph(
        format_selector=format_selector,
        synthesizer=exercise_synthesizer,
        grader=grader,
    )

    def orchestrator_node(state: LearningPlanState) -> dict:
        with span("graph.orchestrator"):
            resp = orchestrator.route_query(state["user_query"])
        return {"route": resp.Assigned_Agent, "reasoning": resp.Reasoning}

    def route_condition(state: LearningPlanState) -> str:
        route = state.get("route")
        if route == "SCOUT":
            return "scout"
        if route == "CONTENT":
            return "content"
        if route == "EXERCISE":
            return "exercise"
        return "placeholder"

    def scout_node(state: LearningPlanState) -> dict:
        with span("graph.scout"):
            out = scout.generate_specialized_queries(with_profile(state["user_query"], state.get("learner_profile")))
        return {"scout_queries": _normalize_scout_queries(out)}

    def academic_node(state: LearningPlanState) -> dict:
        query = state.get("scout_queries", {}).get("ACADEMIC") or state["user_query"]
        with span("graph.academic"):
            resp = academic.provide_academic_roadmap(with_profile(query, state.get("learner_profile")))
        return {"academic_output": resp.content if resp else None}

    async def market_node(state: LearningPlanState) -> dict:
        if not is_technical(state.get("learner_profile")):
            return {"market_output": None}
        query = state.get("scout_queries", {}).get("MARKET") or state["user_query"]
        with span("graph.market"):
            return {"market_output": await _run_market(market, with_profile(query, state.get("learner_profile")))}

    async def practical_node(state: LearningPlanState) -> dict:
        query = state.get("scout_queries", {}).get("PRACTICAL") or state["user_query"]
        with span("graph.practical"):
            if not is_technical(state.get("learner_profile")):
                from agents.factual.factual_agent import FactualAgent
                material = await asyncio.to_thread((factual or FactualAgent()).gather_facts, query)
                return {"practical_output": material}
            repos = await _fetch_github_repos(query)
            # provide_practical_advice uses the SYNC llm.invoke; calling it directly here would block
            # the event loop for the whole LLM call and stall the concurrently-running Market node.
            resp = await asyncio.to_thread(practical.provide_practical_advice, with_profile(query, state.get("learner_profile")), github_repos=repos)
        return {"practical_output": resp.content if resp else None}

    def consensus_node(state: LearningPlanState) -> dict:
        market_data = state.get("market_output")
        market_text = market_data.get("summary") if market_data else None
        if not market_text:
            # Consensus silently substitutes "Not available." — same shape as a grounded run.
            # Say it out loud, otherwise a degraded skill graph is indistinguishable from a good one.
            logger.warning(
                "Consensus degraded: no MARKET grounding for %r "
                "(job scrape returned nothing or timed out) — skill graph built from Academic + Practical only",
                state["user_query"],
            )
        scope_instruction = ""
        scope = state.get("scope")
        lang = state.get("primary_language") or "the detected language"
        if scope == "focused":
            scope_instruction = f"The learner wants a FOCUSED path: only include topics directly relevant to their domain. Use {lang} for all code examples. Do NOT include foundational CS topics in other languages."
        elif scope == "foundation":
            scope_instruction = "The learner wants a STRONG FOUNDATION path: start with fundamentals of the requested subject, then specialize. Do not add unrelated computer science or programming topics."

        grounding_kwargs = {"knowledge_evidence": state["grounding"]} if (state.get("grounding") or {}).get("passages") else {}
        with span("graph.consensus"):
            sg = consensus.build_skill_graph(
                with_profile(state["user_query"], state.get("learner_profile")),
                state.get("academic_output"),
                market_text,
                state.get("practical_output"),
                scope_instruction=scope_instruction,
                **grounding_kwargs,
            )
        # Deterministic coherence repair, same invariant apply_adaptation enforces on adaptations:
        # an edge to a node id the model never emitted becomes an unmasterable prerequisite in
        # services/completion.py, permanently locking its dependants and making is_session_complete
        # unreachable. Drop it here — BEFORE the mermaid render and before the Reviewer — so the
        # persisted graph, the diagram and the review all see the same coherent artifact.
        graph_dict, dropped = prune_dangling_edges(sg)
        try:
            validate_skill_graph(graph_dict)
        except ValueError as exc:
            # One bounded repair request, then fail clearly instead of persisting an impossible path.
            sg = consensus.build_skill_graph(
                with_profile(state["user_query"], state.get("learner_profile")),
                state.get("academic_output"), market_text, state.get("practical_output"),
                scope_instruction=scope_instruction + " Repair structural error: " + str(exc),
                **grounding_kwargs,
            )
            graph_dict, dropped = prune_dangling_edges(sg)
            validate_skill_graph(graph_dict)
        if dropped:
            logger.warning(
                "Consensus graph incoherent for %r: dropped %d edge(s) referencing unknown node ids: %s",
                state["user_query"], len(dropped),
                [(e.get("source"), e.get("target")) for e in dropped],
            )
        evidence = state.get("grounding") or empty_evidence()
        for field in ("summary",):
            if graph_dict.get(field):
                graph_dict[field], evidence = validate_citations(graph_dict[field], evidence)
        for node in graph_dict.get("nodes", []):
            for field in ("label", "description"):
                if node.get(field):
                    node[field], evidence = validate_citations(node[field], evidence)
        return {
            "skill_graph": graph_dict,
            "skill_graph_mermaid": skill_graph_to_mermaid(graph_dict),
            "grounding": evidence,
        }

    async def knowledge_node(state: LearningPlanState) -> dict:
        query = state.get("scout_queries", {}).get("KNOWLEDGE") or state["user_query"]
        try:
            bundle = await asyncio.to_thread(grounding.gather, query, state.get("user_id"),
                                             public_query=state.get("public_query") or state["user_query"])
        except Exception:
            bundle = empty_evidence("unavailable")
        return {"grounding": bundle}

    def reviewer_node(state: LearningPlanState) -> dict:
        sg = state.get("skill_graph")
        if not sg:
            return {"review_passed": False, "review_notes": "No skill graph was produced to review."}
        with span("graph.reviewer"):
            kwargs = {"knowledge_evidence": state["grounding"]} if (state.get("grounding") or {}).get("passages") else {}
            res = reviewer.review_skill_graph(with_profile(state["user_query"], state.get("learner_profile")), json.dumps(sg), **kwargs)
        if not res:
            logger.warning("Reviewer failed to evaluate the skill graph for %r", state["user_query"])
            return {"review_passed": False, "review_notes": "Reviewer failed to evaluate the skill graph."}
        if not res.passed:
            # The verdict rides along in the payload but no consumer acts on it, so a failed review
            # was previously indistinguishable from a passing one in the logs. Say it out loud. The
            # one failure mode we can repair deterministically (dangling edges) is already fixed in
            # consensus_node above, before the Reviewer sees the graph — this covers everything else,
            # which needs a human, not an automatic Consensus retry.
            logger.warning(
                "Reviewer REJECTED the skill graph for %r (serving it anyway): %s",
                state["user_query"], res.notes,
            )
        return {"review_passed": res.passed, "review_notes": res.notes}

    async def content_node(state: LearningPlanState) -> dict:
        out = await content_graph.ainvoke(
            {"user_query": state["user_query"], "format_type": state.get("format_type", "B"), "primary_language": state.get("primary_language"), "learner_profile": state.get("learner_profile"),
             "user_id": state.get("user_id"), "public_query": state.get("public_query") or state["user_query"]}
        )
        return {
            "creative_draft": out.get("creative_draft"),
            "factual_findings": out.get("factual_findings"),
            "final_content": out.get("final_content"),
            "grounding": out.get("grounding"),
        }

    async def exercise_node(state: LearningPlanState) -> dict:
        out = await exercise_graph.ainvoke({"user_query": state["user_query"], "primary_language": state.get("primary_language"), "learner_profile": state.get("learner_profile"),
                                           "user_id": state.get("user_id"), "public_query": state.get("public_query") or state["user_query"]})
        return {
            "exercise_format": out.get("exercise_format"),
            "exercise_statement": out.get("exercise_statement"),
            "grading_artifact": out.get("grading_artifact"),
            "grounding": out.get("grounding"),
        }

    def placeholder_node(state: LearningPlanState) -> dict:
        return {"placeholder": f"Routed to {state.get('route')}. Under development."}

    g = StateGraph(LearningPlanState)
    g.add_node("orchestrator", orchestrator_node)
    g.add_node("scout", scout_node)
    g.add_node("academic", academic_node)
    g.add_node("market", market_node)
    g.add_node("practical", practical_node)
    g.add_node("knowledge", knowledge_node)
    g.add_node("consensus", consensus_node)
    g.add_node("reviewer", reviewer_node)
    g.add_node("content", content_node)
    g.add_node("exercise", exercise_node)
    g.add_node("placeholder", placeholder_node)

    g.add_edge(START, "orchestrator")
    g.add_conditional_edges(
        "orchestrator",
        route_condition,
        {"scout": "scout", "content": "content", "exercise": "exercise", "placeholder": "placeholder"},
    )
    # Learning-plan branch: fan out to specialists, fan in to consensus, then review.
    g.add_edge("scout", "academic")
    g.add_edge("scout", "market")
    g.add_edge("scout", "practical")
    g.add_edge("scout", "knowledge")
    g.add_edge(["academic", "market", "practical", "knowledge"], "consensus")
    g.add_edge("consensus", "reviewer")
    g.add_edge("reviewer", END)
    # Content branch.
    g.add_edge("content", END)
    # Exercise branch.
    g.add_edge("exercise", END)
    # Other routes.
    g.add_edge("placeholder", END)

    # Grounded state can contain private passages. Session persistence owns that data;
    # do not duplicate it into an automatic/shared execution checkpointer.
    return g.compile()
