# LangGraph dual-path Content-Generation DAG (architecture §6.3).
#
#   START --> creative (Path A: engaging draft)
#   START --> factual  (Path B: live web search)
#   creative, factual --> synthesizer (Master LLM merges both)
#   synthesizer --> example (worked code example) --> assembler
#   synthesizer --> visual  (Mermaid diagram)     --> assembler --> END
#
# Each fan-out writes distinct state keys, so the parallel branches need no reducer. The §6.3 tail
# (example + visual generators → deterministic assembler) is gated by MINDMORPH_RICH_CONTENT (default
# on); when off the graph stops at the synthesizer (today's behaviour, no added cost).

import logging
import sys
import os
from typing import Any, Optional, TypedDict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from langgraph.graph import StateGraph, START, END

from agents.content_generator.content_agent import ContentAgent
from agents.example_generator.example_generator_agent import ExampleGeneratorAgent
from agents.factual.factual_agent import FactualAgent
from agents.synthesizer.synthesizer_agent import SynthesizerAgent
from agents.visual_generator.visual_generator_agent import VisualGeneratorAgent

from services.learner_profile import with_profile
from rag.grounding import retrieve_evidence, evidence_prompt, validate_citations

logger = logging.getLogger(__name__)


def _rich_content_enabled() -> bool:
    """§6.3 tail (example + visual + assembler) is on unless MINDMORPH_RICH_CONTENT is explicitly '0'."""
    return os.getenv("MINDMORPH_RICH_CONTENT", "1") != "0"


class ContentState(TypedDict, total=False):
    user_query: str
    format_type: str          # 'A' Boost | 'B' Builder | 'C' Sprint
    prior_feedback: Optional[str]  # score-aware gap guidance; None = plain generation
    path_context: Optional[str]    # overall learning goal — keeps a node lesson in the path's language
    learner_profile: Optional[dict]
    primary_language: Optional[str]
    user_id: Optional[str]    # for per-user RAG retrieval (the user's ingested material)
    public_query: Optional[str]
    grounding: Optional[dict]
    creative_draft: str
    factual_findings: Optional[str]
    final_content: str
    code_example: Optional[str]    # §6.3 tail: worked code example (Markdown fenced block)
    visual_diagram: Optional[str]  # §6.3 tail: Mermaid concept diagram


def _merge_findings(kb: Optional[str], web: Optional[str]) -> Optional[str]:
    """Combine knowledge-base + web grounding into one ``factual_findings`` string.

    Single-source returns that source **verbatim** (keeps the web-only / RAG-off path byte-identical);
    both present are labelled; both None → None (synthesizer degrades to the creative draft)."""
    if kb and web:
        return f"From the knowledge base:\n{kb}\n\nFrom the web:\n{web}"
    return kb or web


def _resolve_retriever(state: ContentState, injected: Optional[Any]) -> Optional[Any]:
    """Pick the RAG retriever for THIS request (resolved per-call so per-user uploads apply).

    Order: an explicitly injected retriever (tests) → the user's ingested store if non-empty → the
    global seed corpus when ``config.RAG_ENABLED`` → None. Lazy imports keep the no-RAG path free of
    fastembed."""
    if injected is not None:
        return injected
    try:
        from rag import registry

        user_id = state.get("user_id")
        user_store = registry.get_user_store(user_id) if user_id else None
        if user_store is not None and not user_store.is_empty:
            return user_store

        import config

        if getattr(config, "RAG_ENABLED", False):
            return registry.get_global_store()
    except Exception:
        logger.exception("RAG: retriever resolution failed; continuing web-only")
    return None


def build_content_graph(
    content: Optional[Any] = None,
    factual: Optional[Any] = None,
    synthesizer: Optional[Any] = None,
    retriever: Optional[Any] = None,
    example: Optional[Any] = None,
    visual: Optional[Any] = None,
    grounding_helper: Optional[Any] = None,
):
    """Build and compile the dual-path content graph. Agents are injectable for tests.

    ``retriever`` optionally pins a RAG store (``rag.store.RagStore``) for every request (tests). When
    omitted, the retriever is resolved **per request** from ``state['user_id']`` (the user's ingested
    material) or the global seed corpus if RAG is enabled. Retrieval *augments* web search, never
    replaces it.

    ``example`` / ``visual`` are the §6.3-tail agents (worked example + Mermaid diagram). They are built
    only when the tail is enabled (``MINDMORPH_RICH_CONTENT`` != '0'); injectable for tests.

    Note: ``MINDMORPH_RICH_CONTENT`` is read **once, here, at build time**. The compiled graph is cached
    per-process (``services.learning_service._get_lesson_graph``), so the flag is a deploy-time setting,
    not a per-request toggle — changing the env after first build has no effect until restart.
    """
    # Preserve the explicit legacy retriever/factual injection surface. Product callers
    # use the common helper; a supplied bundle always takes precedence over fresh research.
    legacy_sources = grounding_helper is None and (factual is not None or retriever is not None)
    grounding_helper = grounding_helper or retrieve_evidence
    content = content or ContentAgent(push_to_langsmith=False)
    factual = factual or FactualAgent()
    synthesizer = synthesizer or SynthesizerAgent(push_to_langsmith=False)
    injected_retriever = retriever
    tail_on = _rich_content_enabled()
    if tail_on:
        example = example or ExampleGeneratorAgent(push_to_langsmith=False)
        visual = visual or VisualGeneratorAgent(push_to_langsmith=False)

    def creative_node(state: ContentState) -> dict:
        fmt = state.get("format_type") or "B"
        return {
            "creative_draft": content.generate_content(
                with_profile(state["user_query"], state.get("learner_profile")), fmt,
                remediation=state.get("prior_feedback"),
                context=state.get("path_context"),
                primary_language=state.get("primary_language"),
            )
        }

    def factual_node(state: ContentState) -> dict:
        query = state["user_query"]
        if state.get("grounding") is not None or not legacy_sources:
            bundle = state.get("grounding")
            if bundle is None:
                bundle = grounding_helper(query, state.get("user_id"), public_query=state.get("public_query"))
            return {"factual_findings": evidence_prompt(bundle), "grounding": bundle}
        web = factual.gather_facts(query)
        retriever = _resolve_retriever(state, injected_retriever)
        kb = retriever.retrieve(query) if retriever else None
        return {"factual_findings": _merge_findings(kb, web)}

    def synthesizer_node(state: ContentState) -> dict:
        final = synthesizer.synthesize(
            with_profile(state["user_query"], state.get("learner_profile")),
            state.get("creative_draft", ""),
            state.get("factual_findings"),
        )
        if state.get("grounding") is not None:
            final, bundle = validate_citations(final, state["grounding"])
            return {"final_content": final, "grounding": bundle}
        return {"final_content": final}

    def example_node(state: ContentState) -> dict:
        if example is None:  # defensive: agent construction failed → degrade, don't crash the graph
            return {"code_example": None}
        return {
            "code_example": example.generate_example(
                state.get("final_content") or "", state["user_query"],
                with_profile(state.get("path_context") or "", {**(state.get("learner_profile") or {}), **({"primary_language": state["primary_language"]} if state.get("primary_language") else {})}) or state.get("path_context"),
            )
        }

    def visual_node(state: ContentState) -> dict:
        if visual is None:
            return {"visual_diagram": None}
        return {
            "visual_diagram": visual.generate_diagram(
                state.get("final_content") or "", state["user_query"],
            )
        }

    def assembler_node(state: ContentState) -> dict:
        # Deterministic merge (no LLM — the synthesizer already did the semantic merge). Appends only the
        # sections that were produced, so a failed/None tail agent never blocks the lesson.
        parts = [state.get("final_content") or ""]
        if state.get("code_example"):
            parts.append(f"## Worked example\n\n{state['code_example']}")
        if state.get("visual_diagram"):
            parts.append(f"## Visual overview\n\n{state['visual_diagram']}")
        final = "\n\n".join(p for p in parts if p)
        if state.get("grounding") is not None:
            final, bundle = validate_citations(final, state["grounding"])
            return {"final_content": final, "grounding": bundle}
        return {"final_content": final}

    g = StateGraph(ContentState)
    g.add_node("creative", creative_node)
    g.add_node("factual", factual_node)
    g.add_node("synthesizer", synthesizer_node)

    g.add_edge(START, "creative")
    g.add_edge(START, "factual")
    g.add_edge(["creative", "factual"], "synthesizer")

    if tail_on:
        g.add_node("example", example_node)
        g.add_node("visual", visual_node)
        g.add_node("assembler", assembler_node)
        g.add_edge("synthesizer", "example")
        g.add_edge("synthesizer", "visual")
        g.add_edge(["example", "visual"], "assembler")
        g.add_edge("assembler", END)
    else:
        g.add_edge("synthesizer", END)

    return g.compile()
