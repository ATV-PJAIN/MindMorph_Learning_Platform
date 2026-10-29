from typing import Optional
from pydantic import BaseModel, Field, model_validator

class FollowUpQuestion(BaseModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    question: str = Field(min_length=1, max_length=500)
    options: list[str] = Field(min_length=2, max_length=5)
    default: int = 0

    @model_validator(mode="after")
    def valid_options(self):
        if not 0 <= self.default < len(self.options) or len(set(self.options)) != len(self.options) or any(not o.strip() or len(o) > 1000 for o in self.options):
            raise ValueError("Invalid follow-up choices")
        return self

class FollowUpQuestions(BaseModel):
    questions: list[FollowUpQuestion] = Field(min_length=1, max_length=4)
    detected_domain: str
    detected_language: Optional[str] = None        # kept for backward compat (programming paths)
    detected_specialization: Optional[str] = None   # generic: "guitar", "financial modeling", "python", etc.
    reasoning: str

    @model_validator(mode="after")
    def unique_questions(self):
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError("Question identifiers must be unique")
        return self
