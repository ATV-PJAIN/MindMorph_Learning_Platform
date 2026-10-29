"""Follow-Up Question Agent: Analyzes user query and generates
personalized clarifying questions before roadmap generation."""

import logging
import os
import sys
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

logger = logging.getLogger(__name__)

from langchain_core.prompts import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    SystemMessagePromptTemplate,
)

from config import get_chat_model
from agents.followup.followup_schema import FollowUpQuestions
from prompts.followup_prompt import FOLLOWUP_SYSTEM_PROMPT, FOLLOWUP_HUMAN_TEMPLATE


class FollowUpAgent:
    """Analyzes user query and generates clarifying questions (structured output)."""

    def __init__(self, push_to_langsmith: bool = False):
        self.llm = get_chat_model("default")
        self.structured_llm = self.llm.with_structured_output(FollowUpQuestions, method="json_schema")
        system_template = SystemMessagePromptTemplate.from_template(FOLLOWUP_SYSTEM_PROMPT)
        human_template = HumanMessagePromptTemplate.from_template(FOLLOWUP_HUMAN_TEMPLATE)
        self.chat_prompt = ChatPromptTemplate.from_messages([system_template, human_template])

    def analyze(self, user_query: str) -> Optional[FollowUpQuestions]:
        """Return FollowUpQuestions, or None on failure / empty input."""
        if not user_query or not isinstance(user_query, str):
            raise ValueError("user_query must be a non-empty string")

        logger.info("FollowUp: analyzing query %r", user_query)
        try:
            result = self.structured_llm.invoke(
                self.chat_prompt.format_messages(user_query=user_query)
            )
            if result is not None:
                logger.info("FollowUp: generated %d question(s)", len(result.questions))
            return result
        except Exception:
            logger.exception("FollowUp: error generating questions")
            return None

if __name__ == "__main__":
    agent = FollowUpAgent()
    print(agent.analyze("Learn MERN stack"))
