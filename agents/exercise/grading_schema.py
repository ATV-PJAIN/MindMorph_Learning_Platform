from typing import List, Optional

from pydantic import BaseModel, Field

from agents.exercise.exercise_schema import CodingLanguageType, ExerciseFormatType


class RubricCriterion(BaseModel):
    """One scored dimension of a case-study / analytical exercise."""

    criterion: str = Field(description="What this criterion evaluates, e.g. 'Correctness of approach'")
    weight: int = Field(description="Relative weight (the weights across the rubric sum to ~100)")


class GradingArtifact(BaseModel):
    """Grading Setup output (architecture §6.4): the auto-grading harness for an exercise.

    For a coding_challenge the grader populates `unit_tests`; for a case_study it populates
    `rubric`. Exactly one is normally filled, matching `format`.
    """

    format: ExerciseFormatType = Field(description="Which exercise format this artifact grades")
    language: Optional[CodingLanguageType] = Field(
        default=None,
        description=(
            "The coding language for a coding_challenge: 'python', 'javascript', or 'sql'. "
            "Determines which execution harness runs the tests."
        ),
    )
    unit_tests: List[str] = Field(
        default_factory=list,
        description=(
            "Test snippets that import the user's solution module. "
            "For Python: pytest-style (import from `solution`). "
            "For JavaScript: node:test + node:assert (require('./solution')). "
            "Populated for coding_challenge (python/javascript); empty for case_study and sql."
        ),
    )
    sql_assertions: List[str] = Field(
        default_factory=list,
        description=(
            "JSON-encoded test assertions for SQL exercises. Each element is a JSON object string "
            "with keys: query (SELECT to run), expected (list of expected row tuples), label (test name). "
            "Populated for coding_challenge (sql); empty otherwise."
        ),
    )
    rubric: List[RubricCriterion] = Field(
        default_factory=list,
        description="Weighted scoring criteria. Populated for case_study; empty for coding_challenge.",
    )
    instructions: str = Field(
        description="Brief instructions to the learner: what to submit and how it will be graded"
    )


class RubricScore(BaseModel):
    """LLM rubric verdict for a case-study submission (Phase B grading)."""

    score: float = Field(description="Overall score from 0 to 100")
    per_criterion: List[str] = Field(
        description="One short '<criterion>: <points/feedback>' line per rubric criterion"
    )
    feedback: str = Field(description="Concise overall feedback: strengths and what to improve")
