import logging
import sys
import os
from typing import Optional

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(PROJECT_ROOT)

logger = logging.getLogger(__name__)

from config import llm
from langchain_core.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate
)
from prompts.content_generator_prompt import CONTENT_PROMPTS
from agents.content_generator.content_agent_personality import ContentAgentPersonality

class ContentAgent:
    """
    Content Generator Agent that uses the LLM to generate lessons
    in various formats (Boost, Builder, Sprint).
    """

    def __init__(self, push_to_langsmith: bool = False):
        self.llm = llm
        self.personality = ContentAgentPersonality()
        # Prompt is dynamic based on format. LangSmith client is created lazily
        # only if/when a push is requested (avoids a network client at import time).
        self.push_to_langsmith = push_to_langsmith

    def generate_content(
        self,
        user_query: str,
        format_type: str = "C",
        remediation: Optional[str] = None,
        context: Optional[str] = None,
        primary_language: Optional[str] = None,
    ) -> str:
        """
        Generates content based on the selected format.
        format_type: 'A' (Boost), 'B' (Builder), 'C' (Sprint)
        remediation: optional "focus on these gaps" guidance from a prior failed attempt.
                     When None (the default), behavior is identical to a plain generation.
        context: optional learning-path goal. Keeps a prerequisite lesson in the path's language/tools
                 (e.g. teach "Programming Fundamentals" in JavaScript for a MERN path, not Python).
        primary_language: explicitly requested programming language for code snippets.
        """
        if not user_query or not isinstance(user_query, str):
            raise ValueError("User query must be a non-empty string")

        logger.info(
            "ContentAgent: generating (%s) lesson for %r (remediation=%s)",
            format_type, user_query, bool(remediation),
        )

        # Select prompt
        system_prompt = CONTENT_PROMPTS.get(format_type.upper(), CONTENT_PROMPTS["C"])

        if primary_language:
            system_prompt = (
                f"{system_prompt}\n\n"
                f"IMPORTANT: All code examples MUST be in {primary_language}. Do not use any other language for code snippets."
            )

        # Keep a prerequisite lesson consistent with the overall path: same language(s), tools, frameworks.
        # Without this, a generic node like "Programming Fundamentals" defaults to Python even on a
        # JavaScript/MERN path. Escape braces (same reason as remediation below).
        if context:
            safe_context = context.replace("{", "{{").replace("}", "}}")
            system_prompt = (
                f"{system_prompt}\n\n"
                "LEARNING-PATH CONTEXT: this skill is one step toward the goal below. Teach it IN THAT "
                "CONTEXT — use the SAME programming language(s), tools, and frameworks as the goal, and "
                "consistent examples. Do NOT switch languages (e.g. never use Python examples for a "
                f"JavaScript/web path):\n{safe_context}"
            )

        # Score-aware regeneration: steer the lesson at the learner's specific gaps.
        # Phase 3: `remediation` now carries real LLM-generated weaknesses, which for a coding
        # platform routinely contain literal `{`/`}` (dict/set literals, f-strings, generics).
        # Escape them before the string reaches SystemMessagePromptTemplate.from_template (below),
        # or they're parsed as template variables and format_messages raises.
        if remediation:
            safe_remediation = remediation.replace("{", "{{").replace("}", "}}")
            system_prompt = (
                f"{system_prompt}\n\n"
                "REMEDIAL FOCUS: the learner previously struggled with the following gaps. "
                "Emphasize and reteach these explicitly, with extra examples:\n"
                f"{safe_remediation}"
            )

        try:
            # Create fresh prompt template for this request
            system_template = SystemMessagePromptTemplate.from_template(system_prompt)
            human_template = HumanMessagePromptTemplate.from_template("Topic: {user_query}")
            chat_prompt = ChatPromptTemplate.from_messages([system_template, human_template])
            
            # TODO: Push to LangSmith if needed (optional logic here)
            
            formatted_prompt = chat_prompt.format_messages(user_query=user_query)
            response = self.llm.invoke(formatted_prompt)
            return response.content
            
        except Exception as e:
            logger.exception("ContentAgent: error generating content")
            return f"Error generating content: {str(e)}"

# Example usage for interactive testing
if __name__ == "__main__":
    agent = ContentAgent(push_to_langsmith=False)
    # Formats: A = 5-min Boost, B = 20-min Builder, C = 2-hour Sprint
    lesson = agent.generate_content("want to learn github", "B")
    print("\n" + "="*50 + "\n")
    print(lesson)
    print("\n" + "="*50 + "\n")
