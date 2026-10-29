"""Exercise-generation graph: format_selector -> [github|blog|dataset] fan-in -> synthesizer -> grader.

All agents mocked (no real LLM / network).
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

from graph.exercise_graph import build_exercise_graph


def _mock_sources():
    github = AsyncMock(return_value="GH_REPOS")
    blog = MagicMock()
    blog.gather_facts.return_value = "BLOG_TUTORIALS"
    dataset = MagicMock()
    dataset.gather_facts.return_value = "DATASET_LINKS"
    return github, blog, dataset


def _mock_grader(artifact_dict):
    grader = MagicMock()
    artifact = MagicMock()
    artifact.model_dump.return_value = artifact_dict
    grader.build_grading_artifact.return_value = artifact
    return grader


async def test_coding_challenge_flow():
    format_selector = MagicMock()
    format_selector.select_format.return_value = MagicMock(format="coding_challenge", reasoning="hands-on", language="python")
    synthesizer = MagicMock()
    synthesizer.synthesize.return_value = "EXERCISE_STATEMENT"
    grader = _mock_grader(
        {"format": "coding_challenge", "unit_tests": ["def test_x(): assert True"], "rubric": [], "instructions": "submit solution"}
    )
    github, blog, dataset = _mock_sources()

    graph = build_exercise_graph(
        format_selector=format_selector,
        synthesizer=synthesizer,
        grader=grader,
        github=github,
        blog=blog,
        dataset=dataset,
    )
    state = await graph.ainvoke({"user_query": "recursion in Python"})

    assert state["exercise_format"] == "coding_challenge"
    # All three source nodes ran and wrote distinct keys (fan-out, no reducer).
    assert state["github_material"] == "GH_REPOS"
    assert state["blog_material"] == "BLOG_TUTORIALS"
    assert state["dataset_material"] == "DATASET_LINKS"
    assert state["exercise_statement"] == "EXERCISE_STATEMENT"
    assert state["grading_artifact"]["unit_tests"]  # coding -> tests populated
    assert state["grading_artifact"]["rubric"] == []
    # Synthesizer saw the chosen format and all three materials.
    synthesizer.synthesize.assert_called_once_with(
        "recursion in Python",
        "coding_challenge",
        exercise_language="python",
        github_material="GH_REPOS",
        blog_material="BLOG_TUTORIALS",
        dataset_material="DATASET_LINKS",
    )
    # Grader runs exactly once after the synthesizer.
    assert grader.build_grading_artifact.call_count == 1


async def test_case_study_flow():
    format_selector = MagicMock()
    format_selector.select_format.return_value = MagicMock(format="case_study", reasoning="analytical", language=None)
    synthesizer = MagicMock()
    synthesizer.synthesize.return_value = "CASE_STUDY_STATEMENT"
    grader = _mock_grader(
        {"format": "case_study", "unit_tests": [], "rubric": [{"criterion": "Depth", "weight": 100}], "instructions": "write analysis"}
    )
    github, blog, dataset = _mock_sources()

    graph = build_exercise_graph(
        format_selector=format_selector,
        synthesizer=synthesizer,
        grader=grader,
        github=github,
        blog=blog,
        dataset=dataset,
    )
    state = await graph.ainvoke({"user_query": "design a recommender system"})

    assert state["exercise_format"] == "case_study"
    assert state["exercise_statement"] == "CASE_STUDY_STATEMENT"
    assert state["grading_artifact"]["rubric"]  # case_study -> rubric populated
    assert state["grading_artifact"]["unit_tests"] == []


async def test_format_selector_failure_defaults_to_coding():
    format_selector = MagicMock()
    format_selector.select_format.return_value = None  # selector failed
    synthesizer = MagicMock()
    synthesizer.synthesize.return_value = "STMT"
    grader = _mock_grader({"format": "coding_challenge", "unit_tests": ["t"], "rubric": [], "instructions": "x"})
    github, blog, dataset = _mock_sources()

    graph = build_exercise_graph(
        format_selector=format_selector,
        synthesizer=synthesizer,
        grader=grader,
        github=github,
        blog=blog,
        dataset=dataset,
    )
    state = await graph.ainvoke({"user_query": "anything"})

    assert state["exercise_format"] == "coding_challenge"
