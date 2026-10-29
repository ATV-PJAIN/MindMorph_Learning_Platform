"""Dual-path content graph: creative + factual fan out (distinct keys) -> synthesizer.

These assert the synthesizer output verbatim, so they run with the §6.3 tail OFF
(MINDMORPH_RICH_CONTENT=0); the tail is covered separately in test_content_tail.py."""
import os
import sys
from unittest.mock import MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

from graph.content_graph import build_content_graph


@pytest.fixture(autouse=True)
def _tail_off(monkeypatch):
    monkeypatch.setenv("MINDMORPH_RICH_CONTENT", "0")


async def test_content_graph_merges_both_paths():
    content = MagicMock()
    content.generate_content.return_value = "DRAFT"
    factual = MagicMock()
    factual.gather_facts.return_value = "FACTS"
    synthesizer = MagicMock()
    synthesizer.synthesize.return_value = "FINAL"

    graph = build_content_graph(content=content, factual=factual, synthesizer=synthesizer)
    state = await graph.ainvoke({"user_query": "Python lists", "format_type": "A"})

    assert state["creative_draft"] == "DRAFT"
    assert state["factual_findings"] == "FACTS"
    assert state["final_content"] == "FINAL"
    content.generate_content.assert_called_once_with("Python lists", "A", remediation=None, context=None, primary_language=None)
    synthesizer.synthesize.assert_called_once_with("Python lists", "DRAFT", "FACTS")


async def test_content_graph_defaults_format_to_builder():
    content = MagicMock()
    content.generate_content.return_value = "DRAFT"
    factual = MagicMock()
    factual.gather_facts.return_value = None
    synthesizer = MagicMock()
    synthesizer.synthesize.return_value = "FINAL"

    graph = build_content_graph(content=content, factual=factual, synthesizer=synthesizer)
    await graph.ainvoke({"user_query": "Python lists"})

    content.generate_content.assert_called_once_with("Python lists", "B", remediation=None, context=None, primary_language=None)


async def test_content_graph_threads_prior_feedback_as_remediation():
    content = MagicMock()
    content.generate_content.return_value = "DRAFT"
    factual = MagicMock()
    factual.gather_facts.return_value = None
    synthesizer = MagicMock()
    synthesizer.synthesize.return_value = "FINAL"

    graph = build_content_graph(content=content, factual=factual, synthesizer=synthesizer)
    await graph.ainvoke(
        {"user_query": "Python lists", "format_type": "A", "prior_feedback": "off-by-one errors"}
    )

    content.generate_content.assert_called_once_with(
        "Python lists", "A", remediation="off-by-one errors", context=None, primary_language=None
    )


async def test_content_graph_passes_profile_language_to_generator():
    content = MagicMock()
    content.generate_content.return_value = "DRAFT"
    factual = MagicMock()
    factual.gather_facts.return_value = None
    synthesizer = MagicMock()
    synthesizer.synthesize.return_value = "FINAL"
    graph = build_content_graph(content=content, factual=factual, synthesizer=synthesizer)
    await graph.ainvoke({"user_query": "Python lists", "primary_language": "python"})
    content.generate_content.assert_called_once_with(
        "Python lists", "B", remediation=None, context=None, primary_language="python"
    )


def test_remediation_with_braces_does_not_break_prompt_template():
    """Phase 3 regression: LLM-generated gaps contain literal braces (dict/set literals, f-strings).
    They must be escaped before from_template, or format_messages raises and the bare except returns
    an 'Error generating content:' string — silently failing the score-aware regeneration."""
    from agents.content_generator.content_agent import ContentAgent

    agent = ContentAgent(push_to_langsmith=False)
    agent.llm = MagicMock()
    agent.llm.invoke.return_value = MagicMock(content="REMEDIAL LESSON")

    out = agent.generate_content("Python dicts", "B", remediation="dict literal {k: v} and set {1, 2}")

    assert out == "REMEDIAL LESSON"
    assert not out.startswith("Error generating content")
    agent.llm.invoke.assert_called_once()
