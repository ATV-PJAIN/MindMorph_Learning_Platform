"""JWT Authentication Middleware for FastAPI.

Extracts Authorization: Bearer <token>, verifies signature and claims,
and attaches verified user_id and email to request.state.
"""
import logging
from typing import Set

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from services.security import decode_access_token

logger = logging.getLogger(__name__)

# Routes that do not require JWT authentication
PUBLIC_PREFIXES = (
    "/auth/login",
    "/auth/register",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)


class JWTAuthenticationMiddleware(BaseHTTPMiddleware):
    """Global middleware enforcing JWT authentication on protected routes."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Always bypass OPTIONS (CORS preflight) and public routes
        if request.method == "OPTIONS" or any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.strip():
            logger.warning(f"Auth failed: Missing Authorization header on protected path {path}")
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication credentials required"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        parts = auth_header.strip().split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            logger.warning(f"Auth failed: Malformed Authorization header on {path}")
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid authentication scheme, Bearer token required"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = parts[1]
        payload = decode_access_token(token)
        if not payload or "sub" not in payload:
            logger.warning(f"Auth failed: Invalid or expired token on {path}")
            return JSONResponse(
                status_code=401,
                content={"detail": "Token expired or invalid"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Attach authenticated user identity to request state
        request.state.user_id = str(payload["sub"])
        request.state.email = str(payload.get("email", ""))
        request.state.jwt_claims = payload

        return await call_next(request)

