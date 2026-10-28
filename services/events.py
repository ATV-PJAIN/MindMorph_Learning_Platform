"""Funnel instrumentation — an append-only event timeline on the learning_session (Gate-1 observability).

`COMMERCIALIZATION.md` Gate 1 needs to see *where learners drop off* and *where generated content is
wrong*. Nothing recorded the journey before this — only `ls["usage"]` (cost) and `ls["node_state"]`
(current mastery). Here every loop action appends one structured event to `ls["events"]`; the timeline
is the single source of truth and `funnel_summary` derives the funnel/drop-off/content-quality
aggregates on read. Persists for free in the JSONB session blob (same path as `usage`).

Mirrors the cost pattern (`services/cost.py`): record raw at one chokepoint per stage, aggregate on read.
"""
import logging
from datetime import datetime, timezone
from typing import Any

from services.mastery import REVIEW_THRESHOLD
from services.telemetry import emit_telemetry

logger = logging.getLogger(__name__)


class STAGES:
    """Event stage names. Constants (not magic strings) so writer and reader can't drift."""

    SESSION_CREATED = "session_created"
    ASSESSMENT_SUBMITTED = "assessment_submitted"
    LESSON_OPENED = "lesson_opened"
    EXERCISE_GRADED = "exercise_graded"
    NODE_MASTERED = "node_mastered"
    NODE_NEEDS_REVIEW = "node_needs_review"
    LESSON_REGENERATED = "lesson_regenerated"
    PATH_COMPLETED = "path_completed"
    COMPOSE_FAILURE = "compose_failure"
    GRADE_FAILURE = "grade_failure"
    CONTENT_FLAGGED = "content_flagged"


def record_event(ls: dict, stage: str, **fields: Any) -> None:
    """Append one event to the session timeline and stream to telemetry/Kafka.
    
    Verified user_id and session_id are embedded in the event for accurate provenance.
    """
    user_id = fields.pop("user_id", None) or ls.get("user_id", "")
    session_id = fields.pop("session_id", None) or ls.get("session_id", "")

    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "user_id": user_id,
        "session_id": session_id,
        **fields,
    }
    ls.setdefault("events", []).append(event)
    try:
        emit_telemetry(event)
    except Exception as e:
        logger.debug("Telemetry emission skipped or failed: %s", e)


def has_event(ls: dict, stage: str) -> bool:
    """True if any event of ``stage`` was already recorded (used to fire once-only events).

    Linear scan — fine for short per-session timelines; revisit with an index if event lists grow large.
    """
    return any(e.get("stage") == stage for e in ls.get("events", []))


def funnel_summary(ls: dict) -> dict:
    """Derive funnel, drop-off, and content-quality aggregates from the raw event timeline.

    Single source of truth: derives everything from ``ls["events"]``; never caches state.
    Missing / empty events timeline safely returns a zeroed summary (legacy-session safe).
    """
    events = ls.get("events", [])
    counts: dict[str, int] = {}
    for e in events:
        stage = e.get("stage")
        if stage:
            counts[stage] = counts.get(stage, 0) + 1

    return {
        "total_events": len(events),
        "stage_counts": counts,
        "reached_grade": counts.get(STAGES.EXERCISE_GRADED, 0) > 0,
        "completed": counts.get(STAGES.PATH_COMPLETED, 0) > 0,
        "low_score_count": counts.get(STAGES.NODE_NEEDS_REVIEW, 0),
        "regenerations": counts.get(STAGES.LESSON_REGENERATED, 0),
        "content_flags": counts.get(STAGES.CONTENT_FLAGGED, 0),
        "failures": (
            counts.get(STAGES.COMPOSE_FAILURE, 0) + counts.get(STAGES.GRADE_FAILURE, 0)
        ),
    }
