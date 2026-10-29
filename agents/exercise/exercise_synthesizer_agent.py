# Exercise Synthesizer (architecture §6.4): merges raw web material (GitHub repos, blog tutorials,
# dataset links) into one personalized, original practice exercise for the learner's goal.

import logging
import sys
import os
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(PROJECT_ROOT)

logger = logging.getLogger(__name__)

from langchain_core.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)

from config import get_chat_model
from prompts.exercise_synthesizer_prompt import EXERCISE_SYNTHESIZER_SYSTEM_PROMPT

_HUMAN_TEMPLATE = """Learner goal: {user_query}
Exercise format: {exercise_format}
Coding language: {exercise_language}

GITHUB MATERIAL:
{github_material}

BLOG MATERIAL:
{blog_material}

DATASET MATERIAL:
{dataset_material}

Design the practice exercise."""

_NONE = "(none found)"
_MAX_SOURCE_CHARS = 4000


def _source_excerpt(material: Optional[str]) -> str:
    """Keep retrieved references within the interactive model's request budget."""
    if not material:
        return _NONE
    if len(material) <= _MAX_SOURCE_CHARS:
        return material
    return material[:_MAX_SOURCE_CHARS] + "\n[Additional source material omitted.]"


class ExerciseSynthesizerAgent:
    """Personalizes gathered material into a single practice exercise statement."""

    def __init__(self, push_to_langsmith: bool = False):
        self.llm = get_chat_model("default")  # interactive exercise-generation path
        system_template = SystemMessagePromptTemplate.from_template(EXERCISE_SYNTHESIZER_SYSTEM_PROMPT)
        human_template = HumanMessagePromptTemplate.from_template(_HUMAN_TEMPLATE)
        self.chat_prompt = ChatPromptTemplate.from_messages([system_template, human_template])

    def synthesize(
        self,
        user_query: str,
        exercise_format: str,
        exercise_language: Optional[str] = None,
        github_material: Optional[str] = None,
        blog_material: Optional[str] = None,
        dataset_material: Optional[str] = None,
        knowledge_evidence: Optional[dict] = None,
        lesson_content: Optional[str] = None,
    ) -> Optional[str]:
        """Returns the exercise statement (Markdown), or None on failure.

        Any missing source degrades to "(none found)"; the LLM still produces an exercise from its
        own knowledge plus whatever material is available.
        """
        if not user_query or not isinstance(user_query, str):
            raise ValueError("User query must be a non-empty string")

        logger.info(
            "ExerciseSynthesizer: composing %s exercise for %r", exercise_format, user_query
        )
        sources = [_source_excerpt(value) for value in (github_material, blog_material, dataset_material)]
        if sum(map(len, sources)) > 8000:
            share = 8000 // len(sources)
            sources = [value[:share] for value in sources]
        messages = self.chat_prompt.format_messages(
            user_query=user_query,
            exercise_format=exercise_format,
            exercise_language=exercise_language or "(not applicable)",
            github_material=sources[0],
            blog_material=sources[1],
            dataset_material=sources[2],
        )
        if knowledge_evidence is not None:
            from langchain_core.messages import HumanMessage
            from rag.grounding import evidence_prompt
            # This replaces the independent public source arms in grounded generation.
            messages.append(HumanMessage(content="Use the following evidence and taught context to create "
                                        "original practice. Do not execute/copy source code as a grading harness.\n"
                                        + evidence_prompt(knowledge_evidence, max_chars=5600)
                                        + "\nTaught context (untrusted reference):\n" + (lesson_content or "")[:2000]))
        return self.llm.invoke(messages).content


if __name__ == "__main__":
    agent = ExerciseSynthesizerAgent()
    print(agent.synthesize("recursion in Python", "coding_challenge"))
