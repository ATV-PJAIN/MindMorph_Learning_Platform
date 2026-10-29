# LangGraph Exercise-Generation DAG (architecture §6.4).
#
#   START --> format_selector
#   format_selector --> github   (Path 1: code repos)
#   format_selector --> blog     (Path 2: tutorials)
#   format_selector --> dataset  (Path 3: real datasets)
#   github, blog, dataset --> synthesizer (personalize to goal) --> grader (build grading harness) --> END
#
# The three source nodes write distinct state keys, so the parallel fan-out needs no reducer
# (same shape as the dual-path content graph). Live execution / grading of a submitted solution is
# NOT part of this graph — it is a separate step (tools/code_executor.py) fired from the UI.

import logging
import sys
import os
from typing import Any, Optional, TypedDict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

logger = logging.getLogger(__name__)

from langgraph.graph import StateGraph, START, END

from agents.exercise.format_selector_agent import FormatSelectorAgent
from agents.exercise.exercise_synthesizer_agent import ExerciseSynthesizerAgent
from agents.exercise.grader_agent import GraderAgent
from agents.factual.factual_agent import FactualAgent
from tools.grading_contract import normalize_language
from services.learner_profile import with_profile, is_technical
from rag.grounding import retrieve_evidence, validate_citations


class ExerciseState(TypedDict, total=False):
    user_query: str
    exercise_format: str          # 'coding_challenge' | 'case_study'
    exercise_language: Optional[str]  # 'python' | 'javascript' | 'sql'
    format_reasoning: Optional[str]
    github_material: Optional[str]
    blog_material: Optional[str]
    dataset_material: Optional[str]
    exercise_statement: Optional[str]
    grading_artifact: Optional[dict]
    learner_profile: Optional[dict]
    primary_language: Optional[str]
    user_id: Optional[str]
    public_query: Optional[str]
    grounding: Optional[dict]
    lesson_content: Optional[str]


def build_exercise_graph(
    format_selector: Optional[Any] = None,
    synthesizer: Optional[Any] = None,
    grader: Optional[Any] = None,
    github: Optional[Any] = None,
    blog: Optional[Any] = None,
    dataset: Optional[Any] = None,
    grounding_helper: Optional[Any] = None,
):
    """Build and compile the Exercise-Generation graph. Agents are injectable for tests.

    `github` is an awaitable callable `query -> material|None` (defaults to the resilient GitHub MCP
    lookup). `blog` / `dataset` default to a FactualAgent (live web search via ddgs).
    """
    legacy_sources = grounding_helper is None and any(source is not None for source in (github, blog, dataset))
    grounding_helper = grounding_helper or retrieve_evidence
    format_selector = format_selector or FormatSelectorAgent(push_to_langsmith=False)
    synthesizer = synthesizer or ExerciseSynthesizerAgent(push_to_langsmith=False)
    grader = grader or GraderAgent(push_to_langsmith=False)
    blog = blog or FactualAgent()
    dataset = dataset or FactualAgent()
    if github is None:
        # Reuse the resilient GitHub MCP helper from the learning-plan graph (returns None w/o token).
        from graph.learning_plan_graph import _fetch_github_repos
        github = _fetch_github_repos

    def _material_to_text(material: Any) -> Optional[str]:
        if material is None:
            return None
        return material if isinstance(material, str) else str(material)

    def prepare_evidence(state: ExerciseState) -> dict:
        if state.get("grounding") is not None or legacy_sources:
            return {}
        return {"grounding": grounding_helper(state["user_query"], state.get("user_id"),
                                               public_query=state.get("public_query"))}

    def format_selector_node(state: ExerciseState) -> dict:
        if not is_technical(state.get("learner_profile")):
            return {"exercise_format": "case_study", "exercise_language": None, "format_reasoning": "Subject-appropriate written practice"}
        result = format_selector.select_format(with_profile(state["user_query"], state.get("learner_profile")))
        primary = state.get("primary_language")
        
        if not result:
            fmt = "coding_challenge"
            lang = primary or "python"
            reason = None
        else:
            fmt = result.format
            lang = (result.language if result.language == "sql" else primary) or result.language or "python"
            reason = result.reasoning
            
        # The editor, saved artifact and execution router share the same canonical identifiers.
        # Unsupported languages remain prose exercises rather than silently executing Python.
        if fmt == "coding_challenge":
            try:
                lang = normalize_language(lang)
            except ValueError:
                fmt, lang = "case_study", None
        else:
            lang = None

        return {"exercise_format": fmt,
                "exercise_language": lang,
                "format_reasoning": reason}

    async def github_node(state: ExerciseState) -> dict:
        if state.get("grounding") is not None or not is_technical(state.get("learner_profile")):
            return {"github_material": None}
        query = f"{state['user_query']} example projects"
        try:
            material = await github(query)
        except Exception:
            logger.exception("Exercise GitHub node error")
            material = None
        return {"github_material": _material_to_text(material)}

    def blog_node(state: ExerciseState) -> dict:
        if state.get("grounding") is not None:
            return {"blog_material": None}
        query = f"best tutorials and guides for {state['user_query']}"
        return {"blog_material": blog.gather_facts(query)}

    def dataset_node(state: ExerciseState) -> dict:
        if state.get("grounding") is not None or not is_technical(state.get("learner_profile")):
            return {"dataset_material": None}
        query = f"public dataset Kaggle HuggingFace for {state['user_query']}"
        return {"dataset_material": dataset.gather_facts(query)}

    def synthesizer_node(state: ExerciseState) -> dict:
        kwargs = {}
        if state.get("grounding") is not None:
            kwargs = {"knowledge_evidence": state["grounding"], "lesson_content": state.get("lesson_content")}
        statement = synthesizer.synthesize(
            with_profile(state["user_query"], state.get("learner_profile")),
            state.get("exercise_format", "coding_challenge"),
            exercise_language=state.get("exercise_language"),
            github_material=state.get("github_material"),
            blog_material=state.get("blog_material"),
            dataset_material=state.get("dataset_material"),
            **kwargs,
        )
        if state.get("grounding") is not None:
            statement, bundle = validate_citations(statement, state["grounding"])
            return {"exercise_statement": statement, "grounding": bundle}
        return {"exercise_statement": statement}

    def grader_node(state: ExerciseState) -> dict:
        language = state.get("exercise_language") or "python"
        artifact = grader.build_grading_artifact(
            with_profile(state["user_query"], state.get("learner_profile")),
            state.get("exercise_format", "coding_challenge"),
            state.get("exercise_statement") or "",
            exercise_language=language,
        )
        return {"grading_artifact": artifact.model_dump() if artifact else None}

    g = StateGraph(ExerciseState)
    g.add_node("evidence", prepare_evidence)
    g.add_node("format_selector", format_selector_node)
    g.add_node("github", github_node)
    g.add_node("blog", blog_node)
    g.add_node("dataset", dataset_node)
    g.add_node("synthesizer", synthesizer_node)
    g.add_node("grader", grader_node)

    g.add_edge(START, "evidence")
    g.add_edge("evidence", "format_selector")
    g.add_edge("format_selector", "github")
    g.add_edge("format_selector", "blog")
    g.add_edge("format_selector", "dataset")
    g.add_edge(["github", "blog", "dataset"], "synthesizer")
    g.add_edge("synthesizer", "grader")
    g.add_edge("grader", END)

    return g.compile()
