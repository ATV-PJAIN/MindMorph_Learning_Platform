"""Streamlit-free orchestration of the adaptive learning loop.

Wraps the existing LangGraph flows (orchestration + lesson compose) and the grade → mastery → adapt
chain so both the Streamlit UI and the FastAPI service drive identical logic over a plain
``learning_session`` dict. LLM-backed agents/graphs are built once per process (lazy singletons).

The async LangGraph ``ainvoke`` calls are wrapped in synchronous helpers (fresh event loop per call,
mirroring the original ``app.py``) so callers — sync FastAPI routes or Streamlit — need no async glue.
"""
import asyncio
import hashlib
import json
import logging
import re
import uuid
from typing import Optional

from services.completion import (
    incomplete_prereq_labels,
    is_session_complete,
    locked_node_ids,
    prereqs_by_node,
)
from services.learner_profile import make_profile, with_profile
from services.cost import TokenMeter
from services.events import STAGES, has_event, record_event
from services.timing import collect, span, spans_payload
from services.generation_privacy import private_generation
from rag.grounding import retrieve_evidence, evidence_prompt, validate_citations
from services.mastery import (
    MASTERY_THRESHOLD,
    REVIEW_THRESHOLD,
    apply_score,
    default_node_state,
    feedback_text,
)

# A correct diagnostic answer is a head-start, not mastery: land between the thresholds so the node
# becomes `in_progress` (path preserved) — one MCQ shouldn't fully master a skill. Passing the node's
# actual exercise (>=MASTERY_THRESHOLD) still upgrades it later.
ASSESSMENT_PASS_SCORE = (REVIEW_THRESHOLD + MASTERY_THRESHOLD) // 2

logger = logging.getLogger(__name__)


def _empty_usage() -> dict:
    """Session-level cost/cache accumulator (unit-economics observability). Persists in the JSONB row."""
    return {"composes": 0, "cache_hits": 0, "tokens_in": 0, "tokens_out": 0, "est_cost_usd": 0.0}

def _normalize_query(q: str) -> str:
    """Produce a canonical topic key from a user query for cache matching."""
    return re.sub(r"\s+", " ", q.lower().strip())

def _cache_topic(query: str, profile: Optional[dict]) -> str:
    """Cache key for a skill graph: the normalized query plus a fingerprint of the learner profile,
    because domain/language/scope/goal/preferences all change the graph that gets planned. A learner
    with no declared profile keeps the bare query key."""
    topic = _normalize_query(query)
    if not profile:
        return topic
    return f"{topic}#{hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()[:12]}"

def _get_graph_from_cache(canonical: str) -> Optional[dict]:
    """Look up an existing skill graph from Neo4j (Phase 3) or Postgres graph_cache (Phase 1).
    Falls through to None on any failure — pipeline runs as normal fallback."""
    # Phase 3: Neo4j is the primary source of truth
    try:
        from persistence.neo4j_graph import topic_graph_exists, read_skill_graph
        if topic_graph_exists(canonical):
            graph = read_skill_graph(canonical)
            if graph:
                logger.info("neo4j: HIT for %r", canonical)
                return graph
    except Exception:
        logger.warning("neo4j read failed (non-fatal), falling back to Postgres", exc_info=True)

    # Phase 1: Postgres fallback
    try:
        from sqlalchemy import text
        from persistence.db import get_sessionmaker
        cache_key = hashlib.sha256(canonical.encode()).hexdigest()
        with get_sessionmaker()() as session:
            row = session.execute(
                text("SELECT skill_graph FROM graph_cache WHERE cache_key = :key"),
                {"key": cache_key},
            ).fetchone()
            if row:
                session.execute(
                    text("UPDATE graph_cache SET hit_count = hit_count + 1, "
                         "last_hit_at = now() WHERE cache_key = :key"),
                    {"key": cache_key},
                )
                session.commit()
                logger.info("graph_cache: HIT for %r", canonical)
                return dict(row[0])
    except Exception:
        logger.warning("graph_cache lookup failed (non-fatal)", exc_info=True)
    return None

def _save_graph_to_cache(canonical: str, query_text: str, skill_graph: dict) -> None:
    """Save a new skill graph to Postgres graph_cache and, if Neo4j is running, to Neo4j."""
    try:
        from sqlalchemy import text
        from persistence.db import get_sessionmaker
        cache_key = hashlib.sha256(canonical.encode()).hexdigest()
        with get_sessionmaker()() as session:
            session.execute(
                text("INSERT INTO graph_cache "
                     "(cache_key, canonical_topic, query_text, skill_graph) "
                     "VALUES (:key, :topic, :query, :graph::jsonb) "
                     "ON CONFLICT (cache_key) DO NOTHING"),
                {"key": cache_key, "topic": canonical,
                 "query": query_text, "graph": json.dumps(skill_graph)},
            )
            session.commit()
    except Exception:
        logger.warning("graph_cache write failed (non-fatal)", exc_info=True)

    # Phase 2: also write to Neo4j (non-fatal if not configured)
    try:
        from persistence.neo4j_graph import write_skill_graph
        write_skill_graph(canonical, skill_graph)
    except Exception as e:
        logger.error(f"neo4j write failed: {e}", exc_info=True)

# --- Lazy per-process graph/agent singletons --------------------------------------------------
_orchestration_graph = None
_lesson_graph = None
_adaptation_agent = None
_assessment_agent = None
_tutor_agent = None
_followup_agent = None


def _run_async(coro):
    """Run an async coroutine to completion on a fresh event loop (matches the original app.py)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _get_orchestration_graph():
    global _orchestration_graph
    if _orchestration_graph is None:
        from agents.orchestrator.orchestrator_agent import OrchestratorAgent
        from agents.scout.scout_agent import ScoutAgent
        from agents.market.market_agent import MarketAnalysisAgent
        from agents.practical.practical_agent import PracticalAgent
        from agents.academic.academic_agent import AcademicAgent
        from graph.learning_plan_graph import build_graph

        logger.info("service: building orchestration graph (one-time)")
        _orchestration_graph = build_graph(
            orchestrator=OrchestratorAgent(push_to_langsmith=False),
            scout=ScoutAgent(push_to_langsmith=False, output_variant="Query"),
            academic=AcademicAgent(push_to_langsmith=False),
            market=MarketAnalysisAgent(),
            practical=PracticalAgent(push_to_langsmith=False),
        )
    return _orchestration_graph


def _get_lesson_graph():
    global _lesson_graph
    if _lesson_graph is None:
        from graph.lesson_graph import build_lesson_graph

        logger.info("service: building lesson graph (one-time)")
        _lesson_graph = build_lesson_graph()
    return _lesson_graph


def _get_adaptation_agent():
    global _adaptation_agent
    if _adaptation_agent is None:
        from agents.adaptation.adaptation_agent import AdaptationAgent

        _adaptation_agent = AdaptationAgent(push_to_langsmith=False)
    return _adaptation_agent


def _get_assessment_agent():
    global _assessment_agent
    if _assessment_agent is None:
        from agents.assessment.skill_assessment_agent import SkillAssessmentAgent

        _assessment_agent = SkillAssessmentAgent(push_to_langsmith=False)
    return _assessment_agent


def _get_followup_agent():
    global _followup_agent
    if _followup_agent is None:
        from agents.followup.followup_agent import FollowUpAgent

        _followup_agent = FollowUpAgent(push_to_langsmith=False)
    return _followup_agent


def generate_followup(query: str) -> dict | None:
    """Run the follow-up agent to generate clarifying questions."""
    agent = _get_followup_agent()
    result = agent.analyze(query)
    return result.model_dump() if result else None


def _run_assessment(skill_graph: dict, learner_profile: Optional[dict] = None) -> Optional[dict]:
    """Generate the diagnostic MCQ quiz for a new path. Best-effort: any failure returns None so path
    creation never fails on the assessment (the frontend then just shows the graph directly)."""
    try:
        quiz = _get_assessment_agent().assess(with_profile(json.dumps(skill_graph), learner_profile))
    except Exception:
        logger.exception("service: assessment generation failed; continuing without a quiz")
        return None
    if quiz is None or not quiz.questions:
        return None
    data = quiz.model_dump()
    for index, question in enumerate(data["questions"]):
        question["id"] = f"q{index + 1}"
    return {"quiz": data, "submitted": False}


class LockedNodeError(Exception):
    """Raised when a lesson is opened for a node whose prerequisites are not yet complete."""

    def __init__(self, node_id: str, pending: list):
        self.node_id = node_id
        self.pending = pending
        super().__init__(f"Node {node_id!r} is locked; complete first: {', '.join(pending)}")


class StaleExerciseError(ValueError):
    pass


@private_generation
def new_learning_session(
    final_state: dict, 
    format_type: str,
    preferences: Optional[dict] = None,
    primary_language: Optional[str] = None,
    scope: Optional[str] = None,
    domain: Optional[str] = None,
) -> Optional[dict]:
    """Build a fresh ``learning_session`` dict from a SCOUT orchestration result.

    Returns ``None`` when no skill graph / nodes were produced. Shape is identical to the original
    ``app.py`` init block (maps 1:1 to the persisted JSONB row).
    """
    profile = make_profile(preferences, primary_language, scope, domain)
    primary_language, scope = profile.get("primary_language"), profile.get("scope")
    skill_graph = final_state.get("skill_graph")
    if not (skill_graph and skill_graph.get("nodes")):
        return None
    from graph.validation import validate_skill_graph
    validate_skill_graph(skill_graph)
    ls = {
        "skill_graph": skill_graph,
        "skill_graph_mermaid": final_state.get("skill_graph_mermaid"),
        "summary": skill_graph.get("summary"),
        "review_passed": final_state.get("review_passed"),
        "review_notes": final_state.get("review_notes"),
        "academic_output": final_state.get("academic_output"),
        "market_output": final_state.get("market_output"),
        "practical_output": final_state.get("practical_output"),
        "format_type": format_type,
        "grounding": final_state.get("grounding"),
        "graph_identity": final_state.get("graph_identity") or uuid.uuid4().hex,
        "public_query": final_state.get("public_query"),
        "node_state": {
            n["id"]: default_node_state() for n in skill_graph["nodes"]
        },
        "lessons": {},
        "selected_node": None,
        "chat": [],  # AI Teaching Assistant turns (P3 #10): list of {role, content}
        "usage": _empty_usage(),  # cost/cache accounting (unit-economics)
        "events": [],  # funnel timeline (Gate-1 observability)
        "canonical": final_state.get("canonical_topic") or _normalize_query(skill_graph.get("summary") or ""),
        "learner_profile": profile,
        "preferences": preferences,
        "primary_language": primary_language,
        "scope": scope,
        "domain": domain,
    }
    record_event(ls, STAGES.SESSION_CREATED,
                 node_count=len(skill_graph["nodes"]), format_type=format_type)
    # Diagnostic onboarding quiz (P2 #8): correct answers later pre-seed mastered nodes. Best-effort —
    # omitted entirely if generation fails, so the path still loads (frontend shows the graph directly).
    assessment = _run_assessment(skill_graph, profile) if profile else _run_assessment(skill_graph)
    if assessment is not None:
        ls["assessment"] = assessment
    return ls


# Human-readable progress labels per LangGraph node, keyed by node name (see graph/learning_plan_graph.py).
# Shared between the streaming create-session route and any future progress UI.
STAGE_LABELS = {
    "orchestrator": "Routing your request",
    "scout": "Scout planning specialist queries",
    "academic": "Academic agent researching",
    "market": "Market agent analyzing job data",
    "practical": "Practical agent finding projects",
    "knowledge": "Finding teaching evidence",
    "consensus": "Building consensus skill graph",
    "reviewer": "Reviewing the skill graph",
    "content": "Composing content",
    "exercise": "Building your exercise",
    "placeholder": "Finishing up",
}


def _route_final_state(final_state: dict, format_type: str, timing: Optional[dict] = None,
                       preferences: Optional[dict] = None,
                       primary_language: Optional[str] = None,
                       scope: Optional[str] = None,
                       domain: Optional[str] = None) -> dict:
    """Shape a finished orchestration-graph state into ``{route, learning_session, final_content,
    exercise}``. Only the SCOUT route yields a persistable ``learning_session`` (the adaptive loop);
    CONTENT/EXERCISE are transient. Shared by ``start_session`` and ``astream_session`` so the
    CONTENT/EXERCISE short-circuit routing can never drift between the sync and streaming paths.
    """
    route = final_state.get("route", "UNKNOWN")
    out = {"route": route, "learning_session": None, "final_content": None, "exercise": None}
    if route == "SCOUT":
        ls = new_learning_session(final_state, format_type, preferences, primary_language, scope, domain)
        # Graph stages run before any session exists, so the spans are collected by the caller and
        # attached here — the one shaping point both the sync and streaming callers go through.
        # CONTENT/EXERCISE routes persist nothing, so their spans stay log-only.
        if ls is not None and timing:
            ls["timing"] = {"graph": timing}
        out["learning_session"] = ls
    elif route == "CONTENT":
        out["final_content"] = final_state.get("final_content")
    elif route == "EXERCISE":
        if final_state.get("exercise_statement"):
            out["exercise"] = {
                "format": final_state.get("exercise_format"),
                "statement": final_state.get("exercise_statement"),
                "grading_artifact": final_state.get("grading_artifact"),
            }
    out["grounding"] = final_state.get("grounding")
    return out


@private_generation
def start_session(user_query: str, format_type: str = "B",
                  preferences: Optional[dict] = None,
                  primary_language: Optional[str] = None,
                  scope: Optional[str] = None,
                  domain: Optional[str] = None,
                  user_id: Optional[str] = None) -> dict:
    """Fresh grounded planning; shared roadmap caches never contain private evidence."""
    profile = make_profile(preferences, primary_language, scope, domain, user_query)
    identity = uuid.uuid4().hex
    inputs = {"user_query": user_query, "public_query": user_query,
              "user_id": user_id, "graph_identity": identity, "format_type": format_type,
              "learner_profile": profile, "preferences": preferences,
              "primary_language": profile.get("primary_language"), "scope": profile.get("scope")}
    with collect() as spans, span("graph.total"):
        final_state = _run_async(_get_orchestration_graph().ainvoke(inputs))
    final_state.update(graph_identity=identity, canonical_topic=identity, public_query=user_query)
    return _route_final_state(final_state, format_type, spans_payload(spans), preferences,
                              profile.get("primary_language"), profile.get("scope"), profile.get("domain"))


@private_generation
async def astream_session(user_query: str, format_type: str = "B",
                          preferences: Optional[dict] = None,
                          primary_language: Optional[str] = None,
                          scope: Optional[str] = None,
                          domain: Optional[str] = None,
                          user_id: Optional[str] = None):
    """Streaming twin with the same caller context and no shared graph cache access."""
    profile = make_profile(preferences, primary_language, scope, domain, user_query)
    identity = uuid.uuid4().hex
    inputs = {"user_query": user_query, "public_query": user_query,
              "user_id": user_id, "graph_identity": identity, "format_type": format_type,
              "learner_profile": profile, "preferences": preferences,
              "primary_language": profile.get("primary_language"), "scope": profile.get("scope")}
    final_state = {}
    with collect() as spans, span("graph.total"):
        async for update in _get_orchestration_graph().astream(inputs, stream_mode="updates"):
            for node_name, node_output in update.items():
                final_state.update(node_output or {})
                yield {"stage": node_name, "label": STAGE_LABELS.get(node_name, node_name)}
    final_state.update(graph_identity=identity, canonical_topic=identity, public_query=user_query)
    result = _route_final_state(final_state, format_type, spans_payload(spans), preferences,
                                profile.get("primary_language"), profile.get("scope"), profile.get("domain"))
    yield {"result": result}


@private_generation
def _run_lesson(
    node: dict,
    format_type: str,
    prior_weaknesses: list,
    user_id: Optional[str] = None,
    path_context: Optional[str] = None,
    primary_language: Optional[str] = None,
    learner_profile: Optional[dict] = None,
    public_query: Optional[str] = None,
) -> tuple[dict, dict]:
    """Invoke the lesson graph for one skill node (content + embedded exercise).

    Returns ``(out, usage)`` where ``usage`` is a ``TokenMeter.totals()`` dict measuring the token
    cost of this compose (aggregated across all nested LLM calls). Wall-clock spans for the compose
    are recorded by ``services.timing`` inside the lesson graph and collected by the caller.
    """
    meter = TokenMeter()
    out = _run_async(
        _get_lesson_graph().ainvoke(
            {
                "skill_label": node.get("label", ""),
                "skill_description": node.get("description", ""),
                "format_type": format_type,
                "prior_weaknesses": prior_weaknesses,
                "user_id": user_id,
                "public_query": public_query,
                "path_context": path_context,
                "learner_profile": learner_profile,
                "primary_language": primary_language,
            },
            config={"callbacks": [meter]},
        )
    )
    return out, meter.totals()


@private_generation
def _repair_missing_grading_artifact(ls: dict, node_id: str) -> None:
    """Retry failed grading setup without replacing the exercise or learner state.

    Generation returns None when its runtime dependencies are unavailable. That None used to be
    cached permanently, even after the runtime was fixed. Existing artifacts are left untouched;
    their normal validation still distinguishes broken tests from learner failures.
    """
    lesson = ls["lessons"][node_id]
    exercise = lesson.get("exercise") or {}
    if (exercise.get("format") != "coding_challenge"
            or exercise.get("grading_artifact") is not None
            or not exercise.get("statement")):
        return

    from agents.exercise.grader_agent import GraderAgent

    logger.info("service: rebuilding missing grading harness for node %s", node_id)
    meter = TokenMeter()
    try:
        artifact = GraderAgent(push_to_langsmith=False).build_grading_artifact(
            with_profile(ls.get("summary") or "Practice this skill", ls.get("learner_profile")),
            exercise["format"],
            exercise["statement"],
            exercise_language=exercise.get("language"),
            callbacks=[meter],
        )
        if artifact is not None:
            exercise["grading_artifact"] = artifact.model_dump()
    except Exception:
        # A failed retry must still leave the submission ungraded, with no mastery changes.
        logger.exception("service: grading harness repair failed for node %s", node_id)
    finally:
        repair_usage = meter.totals()
        lesson["grading_repair_usage"] = repair_usage
        usage = ls.setdefault("usage", _empty_usage())
        for key in ("tokens_in", "tokens_out"):
            usage[key] += repair_usage[key]
        usage["est_cost_usd"] = round(usage["est_cost_usd"] + repair_usage["est_cost_usd"], 6)


@private_generation
def open_lesson(ls: dict, node_id: str, user_id: Optional[str] = None) -> dict:
    """Compose (or reuse cached) the lesson for a node and mark it selected. Mutates and returns ls.

    Enforces prerequisite locking server-side: opening a locked node raises ``LockedNodeError`` so the
    gate cannot be bypassed by a client. Cached content and exercises are reused; only failed grading
    setup is retried. ``user_id`` routes per-user RAG retrieval in the content sub-graph.
    """
    skill_graph = ls["skill_graph"]
    if node_id not in {n["id"] for n in skill_graph["nodes"]}:
        raise ValueError("This skill is not on the roadmap.")
    if node_id in locked_node_ids(skill_graph, ls["node_state"]):
        # A node deterministically locked by a sub-40 grade may have no remedial prerequisites yet (an
        # earlier adaptation LLM call failed). Re-attempt remediation now so the lock is never a
        # permanent dead-end, then re-evaluate before refusing.
        st = ls["node_state"].get(node_id, {})
        if st.get("remediation_pending") and not prereqs_by_node(skill_graph).get(node_id):
            logger.info("service: retrying remediation for flagged node %s on open", node_id)
            adapt_after_grade(ls, node_id)
            skill_graph = ls["skill_graph"]
        if node_id in locked_node_ids(skill_graph, ls["node_state"]):
            pending = incomplete_prereq_labels(skill_graph, ls["node_state"], node_id)
            logger.info("service: blocked locked lesson %s (pending=%s)", node_id, pending)
            raise LockedNodeError(node_id, pending)

    ls["selected_node"] = node_id
    # Sessions created before usage tracking won't have the key; backfill so accounting never KeyErrors.
    usage = ls.setdefault("usage", _empty_usage())
    if node_id not in ls["lessons"]:
        node = next(n for n in skill_graph["nodes"] if n["id"] == node_id)
        prior_weaknesses = ls["node_state"].get(node_id, {}).get("weaknesses", [])
        logger.info("service: composing lesson for node %s", node_id)
        with collect() as spans, span("lesson.total"):
            lesson_kwargs = {"path_context": ls.get("summary")}
            if ls.get("public_query"):
                lesson_kwargs["public_query"] = ls["public_query"]
            if ls.get("learner_profile"):
                lesson_kwargs["learner_profile"] = ls["learner_profile"]
            if ls.get("primary_language") is not None:
                lesson_kwargs["primary_language"] = ls.get("primary_language")
            out, lesson_usage = _run_lesson(
                node, ls.get("format_type", "B"), prior_weaknesses, user_id,
                **lesson_kwargs
            )
        ls["lessons"][node_id] = {
            "content": out.get("content"),
            "grounding": out.get("grounding"),
            "exercise": {
                "revision": uuid.uuid4().hex,
                "format": out.get("exercise_format"),
                "language": out.get("exercise_language"),
                "statement": out.get("exercise_statement"),
                "grounding": out.get("grounding"),
                "grading_artifact": out.get("grading_artifact"),
            },
            "usage": lesson_usage,
            "timing": spans_payload(spans),  # content vs exercise compose, beside the cost meter
        }
        usage["composes"] += 1
        usage["tokens_in"] += lesson_usage["tokens_in"]
        usage["tokens_out"] += lesson_usage["tokens_out"]
        usage["est_cost_usd"] = round(usage["est_cost_usd"] + lesson_usage["est_cost_usd"], 6)
        logger.info(
            "service: lesson %s MISS tokens_in=%d out=%d cost=$%.4f%s",
            node_id, lesson_usage["tokens_in"], lesson_usage["tokens_out"], lesson_usage["est_cost_usd"],
            " (usage unreported)" if lesson_usage["unknown"] else "",
        )
        record_event(ls, STAGES.LESSON_OPENED, node_id=node_id, cache_hit=False)
    else:
        _repair_missing_grading_artifact(ls, node_id)
        usage["cache_hits"] += 1
        logger.info("service: lesson %s HIT (cache_hits=%d)", node_id, usage["cache_hits"])
        record_event(ls, STAGES.LESSON_OPENED, node_id=node_id, cache_hit=True)
    ls["lessons"][node_id].setdefault("exercise", {}).setdefault("revision", uuid.uuid4().hex)
    return ls


@private_generation
def adapt_after_grade(ls: dict, node_id: str) -> list[str]:
    """Phase 3 — close the loop: a graded node mutates the graph (remedial nodes / unlock edges) and,
    on a low score, invalidates the cached lesson so the next open regenerates against the gaps.

    Runs only when the latest grade pushed the node to needs_review or mastered. LLM-backed; any
    failure is best-effort (the grade + mastery already persisted). Mutates ls in place.

    Returns the ids of any remedial/unlock nodes actually added (empty when adaptation didn't run or
    added nothing) — the caller threads this back to the client so the graph rewire can be animated
    instead of snapping to the new layout.
    """
    state = ls["node_state"].get(node_id, {})
    if state.get("status") not in ("needs_review", "mastered"):
        return []

    result = state.get("last_feedback") or {}
    score = float(result.get("score", 0.0) or 0.0)
    feedback = feedback_text(result)

    # Remediation (needs_review) gets bounded retries so the remedial prerequisites are near-certain to
    # land — the deterministic lock holds regardless, but we want an actual unlock path. The mastered
    # (unlock-edge) path is best-effort with a single try; its failure is harmless.
    attempts = 2 if state.get("status") == "needs_review" else 1
    logger.info("service: adapting graph after grade on node %s (score=%.0f)", node_id, score)
    from graph.skill_graph_adapt import apply_adaptation
    graded_node_id = node_id if state.get("status") == "needs_review" else None
    for attempt in range(attempts):
        try:
            adaptation = _get_adaptation_agent().adapt(json.dumps(ls["skill_graph"]), node_id, score, feedback)
            if adaptation is None or (graded_node_id and not adaptation.new_nodes):
                continue
            new_graph, new_ids = apply_adaptation(ls["skill_graph"], adaptation, graded_node_id=graded_node_id)
            break
        except Exception:
            logger.exception("Adaptation attempt failed; keeping the earned grade and original graph")
    else:
        return []
    ls["skill_graph"] = new_graph
    for nid in new_ids:
        ls["node_state"].setdefault(nid, default_node_state())

    focus = list(adaptation.remediation_focus or [])
    if focus:
        # Record the gaps on the graded node and drop its cached lesson so the next open regenerates
        # score-aware content. Clear last_feedback: the regenerated lesson carries a DIFFERENT
        # exercise, so the old grade panel would otherwise render a stale, mismatched result.
        ls["node_state"][node_id] = {**state, "weaknesses": focus, "last_feedback": None}
        ls["lessons"].pop(node_id, None)
        record_event(ls, STAGES.LESSON_REGENERATED, node_id=node_id, focus=focus)

    return new_ids


@private_generation
def grade(ls: dict, node_id: str, solution: str, exercise_revision: Optional[str] = None) -> tuple[dict, list[str], dict | None]:
    """Grade a node's exercise submission, capture mastery, and run adaptation.

    Pulls the format + grading artifact from the cached lesson, so the caller passes only the raw
    solution string. Mutates and returns ``(ls, new_node_ids, result)`` (caller persists the whole
    session afterward); ``new_node_ids`` are any remedial/unlock nodes adaptation just added (empty
    otherwise), so callers can surface the rewire to the client instead of it looking like a silent
    snap. ``result`` is this submission's grade dict — returned separately because ``adapt_after_grade``
    clears ``node_state[node_id].last_feedback`` on the remediation path (the regenerated lesson gets a
    different exercise), which would otherwise leave the client with no score to show the learner.
    """
    if node_id not in {n["id"] for n in ls["skill_graph"]["nodes"]}:
        raise ValueError("This skill is not on the roadmap.")
    if node_id in locked_node_ids(ls["skill_graph"], ls["node_state"]):
        raise LockedNodeError(node_id, incomplete_prereq_labels(ls["skill_graph"], ls["node_state"], node_id))
    lesson = ls["lessons"].get(node_id)
    if not lesson:
        raise ValueError(f"No open lesson for node {node_id!r} to grade.")
    exercise = lesson.get("exercise") or {}
    if exercise.get("revision") is not None and exercise_revision != exercise["revision"]:
        raise StaleExerciseError("This exercise has changed. Reload the lesson before submitting again.")
    fmt = exercise.get("format") or "coding_challenge"
    _repair_missing_grading_artifact(ls, node_id)
    artifact = exercise.get("grading_artifact") or {}

    from agents.exercise.grader_agent import grade_submission

    logger.info("service: grading %s submission for node %s", fmt, node_id)
    # Named for what actually runs: a coding_challenge executes the generated pytest suite in a
    # subprocess, a case_study is an LLM rubric call — lumping both under one name would misattribute
    # an LLM round-trip as test execution.
    exec_stage = "grade.tests" if fmt == "coding_challenge" else "grade.rubric_llm"
    new_node_ids: list[str] = []
    with collect() as spans:
        with span(exec_stage):
            # Older sessions have no explicit exercise language. Keep their Python default while
            # checking declared languages against the saved artifact before executing learner code.
            grade_kwargs = {}
            if exercise.get("language"):
                grade_kwargs["expected_language"] = exercise["language"]
            result = grade_submission(fmt, solution, artifact, **grade_kwargs)
        if result is not None and result.get("harness_error"):
            # The generated grading harness is broken — the learner's submission was never actually
            # measured. Log loudly and record it as a GRADE failure, not an exercise result: no
            # score, no mastery update, no adaptation, no remedial nodes. (apply_score refuses these
            # too, so every caller is covered; this branch exists to keep the 0 out of the funnel.)
            logger.error(
                "service: GRADING HARNESS BROKEN for node %s — generated tests cannot run, "
                "NOT a learner failure: %s", node_id, result.get("failures"),
            )
            record_event(ls, STAGES.GRADE_FAILURE, node_id=node_id, error="harness_error")
        elif result is not None:
            apply_score(ls, node_id, fmt, result)
            score = float(result.get("score", 0.0) or 0.0)
            record_event(ls, STAGES.EXERCISE_GRADED, node_id=node_id, score=score,
                         passed=bool(result.get("passed")))
            # Read the graded node's status BEFORE adapt_after_grade — adaptation only adds new
            # prerequisite nodes, it never changes the graded node's own status, so this snapshot is
            # the final one.
            status = ls["node_state"].get(node_id, {}).get("status")
            if status == "mastered":
                record_event(ls, STAGES.NODE_MASTERED, node_id=node_id)
            elif status == "needs_review":
                record_event(ls, STAGES.NODE_NEEDS_REVIEW, node_id=node_id)
            # adapt_after_grade may emit lesson_regenerated and mutate the graph; run it before the
            # completion check so a freshly-added remedial prerequisite correctly blocks "complete".
            with span("grade.adapt"):
                new_node_ids = adapt_after_grade(ls, node_id)
            if not has_event(ls, STAGES.PATH_COMPLETED) and is_session_complete(
                ls["skill_graph"], ls["node_state"]
            ):
                record_event(ls, STAGES.PATH_COMPLETED)
    # Last grade only — a per-node history here would grow the JSONB blob without bound.
    ls["timing"] = {**ls.get("timing", {}), "grade": spans_payload(spans)}
    return ls, new_node_ids, result


def grade_assessment(ls: dict, answers: list[int]) -> dict:
    """Grade the onboarding MCQ quiz; each correct answer gives that node a head-start. Returns ls.

    A correct answer marks the node ``in_progress`` (``ASSESSMENT_PASS_SCORE``), NOT ``mastered`` — one MCQ
    isn't mastery, and full-mastering everything on a perfect quiz would empty the learning path. The
    learner still studies; passing the node's real exercise upgrades it to mastered. Wrong / unanswered
    (-1) leave the node ``available``. Does NOT run adaptation. Mutates and returns ls.
    """
    assessment = ls.get("assessment")
    if not assessment or not assessment.get("quiz"):
        raise ValueError("No assessment quiz on this session.")
    if assessment.get("submitted"):
        return ls  # retries, double-clicks and later replays cannot change earned progress
    questions = assessment["quiz"].get("questions", [])
    if len(answers) != len(questions):
        raise ValueError(f"Expected {len(questions)} answers, got {len(answers)}.")
    for q, ans in zip(questions, answers):
        if type(ans) is not int or ans < -1 or ans >= len(q.get("options", [])):
            raise ValueError("Assessment answers must be an available option or skipped.")
        if q.get("correct_index") not in range(len(q.get("options", []))):
            raise ValueError("The assessment contains an invalid answer key.")

    correct = 0
    results = []
    for index, (q, ans) in enumerate(zip(questions, answers)):
        q.setdefault("id", f"q{index + 1}")
        is_correct = (ans == q.get("correct_index"))
        results.append({
            "question_id": q["id"],
            "node_id": q.get("node_id"),
            "chosen_index": ans,
            "correct_index": q.get("correct_index"),
            "explanation": q.get("explanation"),
            "is_correct": is_correct
        })
        
        if not is_correct:
            continue  # wrong or skipped (-1) → leave node available
        node_id = q.get("node_id")
        # Guard: the LLM-supplied node_id must be a real node, else it would create a phantom
        # node_state entry and corrupt the complete/total counter.
        if node_id not in ls["node_state"]:
            logger.warning("assessment: skipping unknown node_id %r from quiz", node_id)
            continue
        old_st = ls["node_state"][node_id]
        ls["node_state"][node_id] = {
            **old_st,
            "status": "mastered" if old_st.get("status") == "mastered" or old_st.get("best_score", 0) >= MASTERY_THRESHOLD else "in_progress",
            "best_score": max(old_st.get("best_score", 0), ASSESSMENT_PASS_SCORE),
            "remediation_pending": False if old_st.get("status") == "mastered" or old_st.get("best_score", 0) >= MASTERY_THRESHOLD else old_st.get("remediation_pending", False),
        }
        correct += 1
    assessment["submitted"] = True
    assessment["results"] = results
    record_event(ls, STAGES.ASSESSMENT_SUBMITTED, total=len(questions), correct=correct)
    return ls


# --- AI Teaching Assistant (P3 #10) -----------------------------------------------------------

def _get_tutor_agent():
    global _tutor_agent
    if _tutor_agent is None:
        from agents.tutor.tutor_agent import TutorAgent

        _tutor_agent = TutorAgent()
    return _tutor_agent


def _node_label(ls: dict, node_id: Optional[str]) -> str:
    for n in ls.get("skill_graph", {}).get("nodes", []):
        if n.get("id") == node_id:
            return n.get("label", "")
    return ls.get("summary") or "this topic"


def tutor_retrieval_query(ls: dict, node_id: Optional[str], question: str) -> str:
    node = next((node for node in ls.get("skill_graph", {}).get("nodes", []) if node.get("id") == node_id), {})
    context = ": ".join(str(node.get(key) or "") for key in ("label", "description")).strip(": ")
    return f"{context}\nQuestion: {question}" if context else question


@private_generation
def build_tutor_messages(ls: dict, node_id: Optional[str], question: str,
                         user_id: Optional[str], *, evidence: Optional[dict] = None):
    """Shared fresh evidence plus historical lesson/chat; caller runs this off the event loop."""
    lesson = (ls.get("lessons") or {}).get(node_id) or {}
    bundle = evidence if evidence is not None else retrieve_evidence(
        tutor_retrieval_query(ls, node_id, question), user_id, public_query=ls.get("public_query"))
    return _get_tutor_agent().build_messages(
        skill_label=_node_label(ls, node_id),
        lesson_content=with_profile(lesson.get("content") or "", ls.get("learner_profile")),
        rag_context=evidence_prompt(bundle), history=ls.get("chat", []), question=question)
