# Reviews a Skill Dependency Graph for coherence, completeness, and fitness.

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
from prompts.reviewer_prompt import REVIEWER_SYSTEM_PROMPT
from agents.reviewer.review_schema import ReviewResult
from rag.grounding import evidence_prompt

_HUMAN_TEMPLATE = """Learning goal: {user_query}

Proposed Skill Dependency Graph (JSON):
{skill_graph_json}

Supplied evidence (verify scope, coverage and cited source IDs against this reference):
{knowledge}

Review it."""


class ReviewerAgent:
    '''Checks a Skill Dependency Graph for quality and coherence.'''

    def __init__(self, push_to_langsmith: bool = False):
        self.llm = get_chat_model("complex")  # reviews the skill graph once per session
        self.structured_llm = self.llm.with_structured_output(ReviewResult, method="json_schema")
        system_template = SystemMessagePromptTemplate.from_template(REVIEWER_SYSTEM_PROMPT)
        human_template = HumanMessagePromptTemplate.from_template(_HUMAN_TEMPLATE)
        self.chat_prompt = ChatPromptTemplate.from_messages([system_template, human_template])

    def review_skill_graph(self, user_query: str, skill_graph_json: str,
                          knowledge_evidence: Optional[dict] = None) -> Optional[ReviewResult]:
        '''Returns a ReviewResult, or None on failure.'''
        if not user_query or not isinstance(user_query, str):
            raise ValueError("User query must be a non-empty string")

        logger.info("Reviewer: evaluating skill dependency graph for %r", user_query)
        try:
            messages = self.chat_prompt.format_messages(
                user_query=user_query,
                skill_graph_json=skill_graph_json,
                knowledge=evidence_prompt(knowledge_evidence),
            )
            result = self.structured_llm.invoke(messages)
            if result is not None:
                logger.info("Reviewer: verdict passed=%s", getattr(result, "passed", None))
            return result
        except Exception:
            logger.exception("Reviewer: error reviewing skill graph")
            return None


if __name__ == "__main__":
    agent = ReviewerAgent()
    r = agent.review_skill_graph(
        "Learn Python for data science",
        '{"summary":"x","nodes":[{"id":"python_basics","label":"Python Basics","description":"...","level":"foundational"}],"edges":[]}',
    )
    print(r)
