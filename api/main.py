"""FastAPI application entrypoint.

Run locally:  conda run -n mm uvicorn api.main:app --reload
CORS is opened for the Next.js dev origin (Phase B); tighten ``MINDMORPH_CORS_ORIGINS`` in production.
"""
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from persistence.errors import StorageUnavailableError, SessionConflictError
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

# Validate the LLM key the same way config.py does (the agents need it). Import surfaces the
# ValueError at startup rather than on the first request.
import config  # noqa: E402,F401

from api.auth_routes import auth_router  # noqa: E402
from api.limiter import limiter  # noqa: E402
from api.middleware.auth_middleware import JWTAuthenticationMiddleware  # noqa: E402
from api.routes import router  # noqa: E402

app = FastAPI(title="MindMorph API", version="1.0.0")

# Attach slowapi rate limiter state and exception handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# 1. Rate Limiting Middleware
app.add_middleware(SlowAPIMiddleware)

# 2. JWT Authentication & Security Middleware
app.add_middleware(JWTAuthenticationMiddleware)

# 3. CORS Middleware
_default_origins = "http://localhost:3000,http://127.0.0.1:3000"
_origins = os.getenv("MINDMORPH_CORS_ORIGINS", _default_origins).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip().rstrip("/") for o in _origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(auth_router)
app.include_router(router)


@app.exception_handler(StorageUnavailableError)
async def storage_unavailable(_request, _exc):
    return JSONResponse(status_code=503, content={"detail": "Progress could not be saved or loaded. Please try again."})


@app.exception_handler(SessionConflictError)
async def session_conflict(_request, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "store": os.getenv("MINDMORPH_STORE", "postgres")}


@app.get("/ready")
def ready():
    store = os.getenv("MINDMORPH_STORE", "postgres").lower()
    if store == "memory":
        return {"status": "ready", "store": store, "durable": False}
    try:
        if store != "postgres":
            raise ValueError("Unknown store")
        from persistence.db import check_ready
        check_ready()
    except Exception:
        logger.exception("Database readiness check failed")
        return JSONResponse(status_code=503, content={"status": "not_ready", "store": store,
                            "detail": "Database unavailable or migrations pending. Run alembic upgrade head."})
    return {"status": "ready", "store": store, "durable": True}
