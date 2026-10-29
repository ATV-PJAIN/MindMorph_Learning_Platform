# LangGraph Lesson DAG — composes the existing content + exercise sub-graphs sequentially.
#
#   START --> content --> exercise --> END
#
# A "lesson" = a piece of content for a *single clicked skill node* with an exercise at the end.
# The content runs first (the teaching), then the exercise (the practice). The two compiled
# sub-graphs are reused unchanged; this parent only sequences them and pins the query to the
# clicked node (label + description), NOT the learner's original top-level query.

import logging
import sys
import os
from typing import Any, Optional, TypedDict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

logger = logging.getLogger(__name__)

from langgraph.graph import StateGraph, START, END

from graph.content_graph import build_content_graph
from graph.exercise_graph import build_exercise_graph
from services.timing import span


class LessonState(TypedDict, total=False):
    # Inputs (set by the caller for the clicked skill node).
    skill_label: str
    skill_description: str
    format_type: str                  # 'A' Boost | 'B' Builder | 'C' Sprint
    prior_score: Optional[float]
    prior_weaknesses: Optional[list]  # list[str] gap topics from a prior failed attempt
    user_id: Optional[str]            # for per-user RAG retrieval in the content sub-graph
    path_context: Optional[str]       # overall goal — keeps the lesson in the path's language
    learner_profile: Optional[dict]
    primary_language: Optional[str]
    public_query: Optional[str]
    grounding: Optional[dict]
    # Derived.
    skill_query: str
    # Outputs.
    content: Optional[str]
    exercise_format: Optional[str]
    exercise_language: Optional[str]   # 'python' | 'javascript' | 'sql'
    exercise_statement: Optional[str]
    grading_artifact: Optional[dict]


def _skill_query(state: LessonState) -> str:
    """The lesson/exercise are about the clicked node, not the top-level query.

    Easiest thing to get wrong: pin the query to `label: description`.
    """
    label = (state.get("skill_label") or "").strip()
    description = (state.get("skill_description") or "").strip()
    if label and description:
        return f"{label}: {description}"
    return label or description


def build_lesson_graph(content_graph: Optional[Any] = None, exercise_graph: Optional[Any] = None):
    """Build and compile the Lesson graph. The two sub-graphs are injectable for tests;
    defaults are the real compiled content + exercise graphs."""
    content_graph = content_graph or build_content_graph()
    exercise_graph = exercise_graph or build_exercise_graph()

    async def content_node(state: LessonState) -> dict:
        skill_query = _skill_query(state)
        weaknesses = state.get("prior_weaknesses") or []
        prior_feedback = ", ".join(weaknesses) if weaknesses else None
        logger.info("Lesson: composing content for skill %r", skill_query)
        with span("lesson.content"):
            out = await content_graph.ainvoke(
                {
                    "user_query": skill_query,
                    "format_type": state.get("format_type", "B"),
                    "prior_feedback": prior_feedback,
                    "user_id": state.get("user_id"),
                    "public_query": state.get("public_query"),
                    "path_context": state.get("path_context"),
                    "primary_language": state.get("primary_language"),
                    "learner_profile": state.get("learner_profile"),
                }
            )
        return {"skill_query": skill_query, "content": out.get("final_content"),
                "grounding": out.get("grounding")}

    async def exercise_node(state: LessonState) -> dict:
        # Reuse the skill_query the content node already derived (falls back if absent).
        skill_query = state.get("skill_query") or _skill_query(state)
        logger.info("Lesson: composing exercise for skill %r", skill_query)
        with span("lesson.exercise"):
            out = await exercise_graph.ainvoke({
                "user_query": skill_query,
                "primary_language": state.get("primary_language"),
                "learner_profile": state.get("learner_profile"),
                "user_id": state.get("user_id"),
                "public_query": state.get("public_query"),
                "grounding": state.get("grounding"),
                "lesson_content": state.get("content"),
            })
        return {
            "exercise_format": out.get("exercise_format"),
            "exercise_language": out.get("exercise_language"),
            "exercise_statement": out.get("exercise_statement"),
            "grading_artifact": out.get("grading_artifact"),
            "grounding": out.get("grounding") or state.get("grounding"),
        }

    g = StateGraph(LessonState)
    g.add_node("content", content_node)
    g.add_node("exercise", exercise_node)

    g.add_edge(START, "content")
    g.add_edge("content", "exercise")
    g.add_edge("exercise", END)

    return g.compile()
