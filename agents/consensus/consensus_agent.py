# Combines the Academic / Market / Practical findings into a single Skill Dependency Graph.

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
from prompts.consensus_prompt import CONSENSUS_SYSTEM_PROMPT
from agents.consensus.skill_graph_schema import SkillGraph
from rag.grounding import evidence_prompt

_HUMAN_TEMPLATE = """Learning goal: {user_query}

ACADEMIC perspective:
{academic}

MARKET perspective:
{market}

PRACTICAL perspective:
{practical}

KNOWLEDGE EVIDENCE:
{knowledge}

Reconcile these into a Skill Dependency Graph."""


class ConsensusAgent:
    '''Synthesizes specialist findings into a structured Skill Dependency Graph.'''

    def __init__(self, push_to_langsmith: bool = False):
        self.llm = get_chat_model("complex")  # builds the skill graph once per session
        self.structured_llm = self.llm.with_structured_output(SkillGraph, method="json_schema")
        system_template = SystemMessagePromptTemplate.from_template(CONSENSUS_SYSTEM_PROMPT)
        human_template = HumanMessagePromptTemplate.from_template(_HUMAN_TEMPLATE)
        self.chat_prompt = ChatPromptTemplate.from_messages([system_template, human_template])

    def build_skill_graph(
        self,
        user_query: str,
        academic: Optional[str],
        market: Optional[str],
        practical: Optional[str],
        scope_instruction: str = "",
        knowledge_evidence: Optional[dict] = None,
    ) -> Optional[SkillGraph]:
        '''Returns a SkillGraph reconciling the three perspectives, or None on failure.'''
        if not user_query or not isinstance(user_query, str):
            raise ValueError("User query must be a non-empty string")

        logger.info("Consensus: building skill dependency graph for %r", user_query)
        try:
            messages = self.chat_prompt.format_messages(
                user_query=user_query,
                academic=academic or "Not available.",
                market=market or "Not available.",
                practical=practical or "Not available.",
                knowledge=evidence_prompt(knowledge_evidence),
                scope_instruction=scope_instruction,
            )
            graph = self.structured_llm.invoke(messages)
            logger.info(
                "Consensus: built skill graph with %d nodes",
                len(graph.nodes) if graph else 0,
            )
            return graph
        except Exception:
            logger.exception("Consensus: error building skill graph")
            return None


if __name__ == "__main__":
    agent = ConsensusAgent()
    g = agent.build_skill_graph(
        "Learn Python for data science",
        academic="Start with Python syntax, then statistics, then ML.",
        market="Pandas, scikit-learn, and SQL are most in demand.",
        practical="Build a data-cleaning project, then a regression model.",
    )
    print(g)
