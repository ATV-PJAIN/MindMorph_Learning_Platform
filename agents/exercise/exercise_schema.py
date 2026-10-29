from typing import Literal, Optional

from pydantic import BaseModel, Field

# The two exercise shapes MindMorph supports (architecture §6.4 Format Selector).
ExerciseFormatType = Literal["coding_challenge", "case_study"]

# Languages with auto-grading support (code execution harness in tools/code_executor.py).
CodingLanguageType = Literal["python", "javascript", "sql"]


class ExerciseFormat(BaseModel):
    """Format Selector verdict: which kind of exercise best fits the learning goal."""

    format: ExerciseFormatType = Field(
        description=(
            "'coding_challenge' for hands-on programming the user solves with code; "
            "'case_study' for analytical/design tasks judged against a rubric."
        )
    )
    language: Optional[CodingLanguageType] = Field(
        default=None,
        description=(
            "The coding language for a coding_challenge: 'python', 'javascript', or 'sql'. "
            "Must be set when format is 'coding_challenge'; ignored for 'case_study'."
        ),
    )
    reasoning: str = Field(description="One sentence on why this format suits the request")
