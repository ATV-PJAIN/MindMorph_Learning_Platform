"""Schemas for the dynamic skill assessment (P2 #8).

A short MCQ quiz generated from the skill graph; correct answers pre-seed which nodes the learner
already knows. ``node_id`` ties each question back to a real ``SkillNode.id``.
"""
from typing import List

from pydantic import BaseModel, Field, field_validator


class MCQQuestion(BaseModel):
    node_id: str = Field(
        description="The skill node id this question assesses. MUST exactly match an id from the graph."
    )
    question: str = Field(description="A single multiple-choice question gauging that skill.")
    options: List[str] = Field(min_length=4, max_length=4, description="Exactly 4 distinct answer options.")
    correct_index: int = Field(ge=0, le=3, description="0-based index into options of the single correct answer.")
    explanation: str = Field(default="", description="One sentence on why the answer is correct.")

    @field_validator("options")
    @classmethod
    def distinct_options(cls, values):
        if any(not value.strip() for value in values) or len({value.strip() for value in values}) != 4:
            raise ValueError("Assessment options must be nonempty and distinct")
        return values


class AssessmentQuiz(BaseModel):
    questions: List[MCQQuestion] = Field(description="One question per assessed skill node.")
