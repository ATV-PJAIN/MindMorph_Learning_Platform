"""HTTP routes for the adaptive learning loop.

Pattern, every mutating endpoint: load the session from the repository → call a ``services`` function
that mutates the dict → save the whole dict back → return it. The repository is resolved per request
(``get_default_repository``), so the store (Postgres / in-memory) is swappable via config.

Now secured with JWT authentication & multi-tenant isolation:
Every request extracts verified user_id from request.state.user_id (populated by JWTAuthenticationMiddleware).
Cross-tenant access returns 403 Forbidden.
Rate limits applied via slowapi annotations.
"""
import json
import asyncio
import logging
import os
import secrets
import uuid

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from api.limiter import limiter
from api.schemas import (
    AssessmentAnswersRequest,
    ChatRequest,
    CreateSessionRequest,
    FlagRequest,
    FollowUpRequest,
    FollowUpResponse,
    GradeRequest,
    IngestResponse,
    SessionMeta,
    SessionResponse,
    StartSessionResponse,
)
from persistence.repository import get_default_repository
from persistence.errors import StorageUnavailableError, SessionConflictError
from services.events import STAGES, funnel_summary, record_event
from services.generation_privacy import private_generation
from rag.grounding import retrieve_evidence, validate_citations, CitationStream
from services.learning_service import (
    LockedNodeError,
    StaleExerciseError,
    astream_session,
    build_tutor_messages,
    generate_followup,
    grade,
    grade_assessment,
    open_lesson,
    start_session,
    tutor_retrieval_query,
)

logger = logging.getLogger(__name__)
router = APIRouter()

GEN_FAILED = "The learning service is busy or rate-limited. Please try again in a moment."


def _service_unavailable(what: str, exc: Exception) -> HTTPException:
    logger.error("api: %s failed (%s)", what, type(exc).__name__)
    return HTTPException(status_code=503, detail=GEN_FAILED)


def _get_authed_user_id(request: Request, fallback_user_id: str | None = None) -> str:
    """Return authenticated user_id from middleware state, with optional fallback for legacy/internal calls."""
    uid = getattr(request.state, "user_id", None)
    if uid:
        return uid
    if fallback_user_id:
        return fallback_user_id
    raise HTTPException(status_code=401, detail="Authentication required")


def _load_or_404(repo, authed_user_id: str, session_id: str, target_user_id: str | None = None) -> dict:
    """Load session and verify multi-tenant ownership."""
    if target_user_id and target_user_id != authed_user_id:
        raise HTTPException(
            status_code=403, detail="Forbidden: You do not have permission to access another user's session"
        )
    ls = repo.get(authed_user_id, session_id)
    if ls is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return ls


@router.post("/sessions/followup", response_model=FollowUpResponse)
@limiter.limit("15/minute")
def get_followup_questions(request: Request, req: FollowUpRequest) -> FollowUpResponse:
    """Generate follow-up questions before creating a session."""
    try:
        result = generate_followup(req.query)
        if not result:
            raise ValueError("Failed to generate follow-up questions")
    except Exception as e:
        raise _service_unavailable("followup", e)
    return FollowUpResponse(**result)


@router.post("/sessions", response_model=StartSessionResponse)
@limiter.limit("10/minute")
def create_session(request: Request, req: CreateSessionRequest) -> StartSessionResponse:
    """Run orchestration for a query. Scoped strictly to the authenticated user."""
    authed_user_id = _get_authed_user_id(request, req.user_id)
    extra = {"user_id": authed_user_id}
    if req.preferences is not None:
        extra["preferences"] = req.preferences
    if req.primary_language is not None:
        extra["primary_language"] = req.primary_language
    if req.scope is not None:
        extra["scope"] = req.scope
    if req.domain is not None:
        extra["domain"] = req.domain

    try:
        result = start_session(req.query, req.format_type, **extra)
    except Exception as e:
        raise _service_unavailable("start_session", e)
    resp = StartSessionResponse(
        route=result["route"],
        final_content=result.get("final_content"),
        exercise=result.get("exercise"),
        grounding=result.get("grounding"),
    )
    ls = result.get("learning_session")
    if ls is not None:
        session_id = ls.get("graph_identity") or uuid.uuid4().hex
        ls["session_id"] = session_id
        ls["user_id"] = authed_user_id
        get_default_repository().save(authed_user_id, session_id, ls, title=req.query)
        resp.session_id = session_id
        resp.learning_session = ls
    return resp


@router.post("/sessions/stream")
@limiter.limit("10/minute")
async def create_session_stream(request: Request, req: CreateSessionRequest) -> StreamingResponse:
    """Streaming twin of POST /sessions: SSE progress with verified user identity."""
    authed_user_id = _get_authed_user_id(request, req.user_id)

    extra = {"user_id": authed_user_id}
    if req.preferences is not None:
        extra["preferences"] = req.preferences
    if req.primary_language is not None:
        extra["primary_language"] = req.primary_language
    if req.scope is not None:
        extra["scope"] = req.scope
    if req.domain is not None:
        extra["domain"] = req.domain

    async def gen():
        try:
            async for evt in astream_session(req.query, req.format_type, **extra):
                if "result" not in evt:
                    yield _sse({"stage": evt["stage"], "label": evt["label"]})
                    continue
                result = evt["result"]
                resp = StartSessionResponse(
                    route=result["route"],
                    final_content=result.get("final_content"),
                    exercise=result.get("exercise"),
                    grounding=result.get("grounding"),
                )
                ls = result.get("learning_session")
                if ls is not None:
                    session_id = ls.get("graph_identity") or uuid.uuid4().hex
                    ls["session_id"] = session_id
                    ls["user_id"] = authed_user_id
                    get_default_repository().save(authed_user_id, session_id, ls, title=req.query)
                    resp.session_id = session_id
                    resp.learning_session = ls
                yield _sse({"done": True, "session": resp.model_dump()})
        except (StorageUnavailableError, SessionConflictError) as exc:
            yield _sse({"error": str(exc)})
        except Exception:
            logger.error("api: create_session_stream failed")
            yield _sse({"error": GEN_FAILED})

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/sessions/{user_id}", response_model=list[SessionMeta])
def list_sessions(request: Request, user_id: str) -> list[SessionMeta]:
    authed_user_id = _get_authed_user_id(request, user_id)
    if user_id != authed_user_id:
        raise HTTPException(status_code=403, detail="Forbidden: Cannot list sessions for another user")
    return [SessionMeta(**m) for m in get_default_repository().list_sessions(authed_user_id)]


@router.get("/sessions/{user_id}/{session_id}", response_model=SessionResponse)
def get_session(request: Request, user_id: str, session_id: str) -> SessionResponse:
    authed_user_id = _get_authed_user_id(request, user_id)
    repo = get_default_repository()
    ls = _load_or_404(repo, authed_user_id, session_id, target_user_id=user_id)
    return SessionResponse(session_id=session_id, learning_session=ls)


@router.post("/sessions/{user_id}/{session_id}/lessons/{node_id}", response_model=SessionResponse)
@limiter.limit("30/minute")
def open_node_lesson(request: Request, user_id: str, session_id: str, node_id: str) -> SessionResponse:
    authed_user_id = _get_authed_user_id(request, user_id)
    repo = get_default_repository()
    ls = _load_or_404(repo, authed_user_id, session_id, target_user_id=user_id)
    try:
        ls = open_lesson(ls, node_id, user_id=authed_user_id)
    except LockedNodeError as e:
        repo.save(authed_user_id, session_id, ls)
        raise HTTPException(status_code=409, detail={"error": "locked", "pending": e.pending})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        record_event(ls, STAGES.COMPOSE_FAILURE, node_id=node_id, error=type(e).__name__, user_id=authed_user_id)
        repo.save(authed_user_id, session_id, ls)
        raise _service_unavailable("open_lesson", e)
    repo.save(authed_user_id, session_id, ls)
    return SessionResponse(session_id=session_id, learning_session=ls)


@router.post("/users/{user_id}/knowledge", response_model=IngestResponse)
@limiter.limit("10/minute")
async def ingest_material(request: Request, user_id: str, file: UploadFile = File(...)) -> IngestResponse:
    """Ingest a user's PDF into their personal RAG store; scoped to authenticated user."""
    authed_user_id = _get_authed_user_id(request, user_id)
    if user_id != authed_user_id:
        raise HTTPException(status_code=403, detail="Forbidden: Cannot upload files for another user")
    name = file.filename or "upload.pdf"
    if not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported.")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    from rag import registry

    try:
        chunks = await asyncio.to_thread(registry.ingest_pdf, authed_user_id, data, name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _service_unavailable("ingest_material", e)
    return IngestResponse(filename=name, chunks=chunks)


@router.post("/sessions/{user_id}/{session_id}/assessment", response_model=SessionResponse)
@limiter.limit("15/minute")
def grade_session_assessment(
    request: Request, user_id: str, session_id: str, req: AssessmentAnswersRequest
) -> SessionResponse:
    authed_user_id = _get_authed_user_id(request, user_id)
    repo = get_default_repository()
    ls = _load_or_404(repo, authed_user_id, session_id, target_user_id=user_id)
    try:
        ls = grade_assessment(ls, req.answers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _service_unavailable("grade_assessment", e)
    repo.save(authed_user_id, session_id, ls)
    return SessionResponse(session_id=session_id, learning_session=ls)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/sessions/{user_id}/{session_id}/chat")
@limiter.limit("20/minute")
async def chat(request: Request, user_id: str, session_id: str, req: ChatRequest) -> StreamingResponse:
    authed_user_id = _get_authed_user_id(request, user_id)
    repo = get_default_repository()
    ls = _load_or_404(repo, authed_user_id, session_id, target_user_id=user_id)
    ls.setdefault("chat", [])
    ls["chat"].append({"role": "user", "content": req.message})
    repo.save(authed_user_id, session_id, ls)

    evidence = await asyncio.to_thread(
        retrieve_evidence,
        tutor_retrieval_query(ls, req.node_id, req.message),
        authed_user_id,
        public_query=ls.get("public_query"),
    )
    messages = await asyncio.to_thread(
        build_tutor_messages, ls, req.node_id, req.message, authed_user_id, evidence=evidence
    )
    from services.learning_service import _get_tutor_agent

    @private_generation
    async def gen():
        full = ""
        citations = CitationStream(evidence)
        try:
            async for token in _get_tutor_agent().astream(messages):
                full += token
                ready = citations.push(token)
                if ready:
                    yield _sse({"token": ready})
            tail = citations.push("", final=True)
            if tail:
                yield _sse({"token": tail})
        except Exception:
            logger.error("api: chat stream failed")
            yield _sse({"error": GEN_FAILED})
            return

        full, final_evidence = validate_citations(full, evidence)
        ls["chat"].append({"role": "assistant", "content": full, "grounding": final_evidence})
        try:
            repo.save(authed_user_id, session_id, ls)
        except (StorageUnavailableError, SessionConflictError) as exc:
            yield _sse({"error": str(exc)})
            return
        yield _sse({"grounding": final_evidence})
        yield _sse({"done": True})

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/sessions/{user_id}/{session_id}/grade", response_model=SessionResponse)
@limiter.limit("15/minute")
def grade_node(
    request: Request, user_id: str, session_id: str, node_id: str, req: GradeRequest
) -> SessionResponse:
    authed_user_id = _get_authed_user_id(request, user_id)
    repo = get_default_repository()
    ls = _load_or_404(repo, authed_user_id, session_id, target_user_id=user_id)
    new_node_ids: list[str] = []
    result: dict | None = None
    try:
        extra = {"exercise_revision": req.exercise_revision} if req.exercise_revision is not None else {}
        ls, new_node_ids, result = grade(ls, node_id, req.solution, **extra)
    except LockedNodeError as e:
        raise HTTPException(status_code=409, detail={"error": "locked", "pending": e.pending})
    except StaleExerciseError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        record_event(ls, STAGES.GRADE_FAILURE, node_id=node_id, error=type(e).__name__, user_id=authed_user_id)
        repo.save(authed_user_id, session_id, ls)
        raise _service_unavailable("grade", e)
    repo.save(authed_user_id, session_id, ls)
    return SessionResponse(
        session_id=session_id,
        learning_session=ls,
        new_node_ids=new_node_ids,
        grade_result=result,
    )


@router.post("/sessions/{user_id}/{session_id}/lessons/{node_id}/flag", response_model=SessionResponse)
def flag_lesson(
    request: Request, user_id: str, session_id: str, node_id: str, req: FlagRequest
) -> SessionResponse:
    authed_user_id = _get_authed_user_id(request, user_id)
    repo = get_default_repository()
    ls = _load_or_404(repo, authed_user_id, session_id, target_user_id=user_id)
    record_event(ls, STAGES.CONTENT_FLAGGED, node_id=node_id, reason=req.reason, user_id=authed_user_id)
    repo.save(authed_user_id, session_id, ls)
    return SessionResponse(session_id=session_id, learning_session=ls)


@router.get("/admin/funnel")
def admin_funnel(x_admin_token: str | None = Header(default=None)) -> dict:
    """Aggregate the Gate-1 funnel across every session (operator view)."""
    expected = os.environ.get("MINDMORPH_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="Admin funnel disabled (MINDMORPH_ADMIN_TOKEN unset).")
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=403, detail="Forbidden")

    sessions = get_default_repository().list_all()
    summaries = [funnel_summary(ls) for _, _, ls in sessions]
    stage_reach: dict[str, int] = {}
    for s in summaries:
        for stage in s["stage_counts"]:
            stage_reach[stage] = stage_reach.get(stage, 0) + 1
    return {
        "total_sessions": len(summaries),
        "reached_grade": sum(1 for s in summaries if s["reached_grade"]),
        "completed": sum(1 for s in summaries if s["completed"]),
        "sessions_reaching_stage": stage_reach,
        "low_score_total": sum(s["low_score_count"] for s in summaries),
        "regenerations_total": sum(s["regenerations"] for s in summaries),
        "content_flags_total": sum(s["content_flags"] for s in summaries),
        "failures_total": sum(s["failures"] for s in summaries),
    }
