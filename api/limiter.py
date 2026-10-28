"""SlowAPI rate limiter instance and key resolver functions."""
import os
from typing import Optional
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

RATELIMIT_ENABLED = os.getenv("RATELIMIT_ENABLED", "true").lower() in ("true", "1", "yes")
RATELIMIT_STORAGE_URL = os.getenv("RATELIMIT_STORAGE_URL", "memory://")
DEFAULT_RATELIMIT = os.getenv("RATELIMIT_DEFAULT", "100/minute")


def get_user_or_ip_key(request: Request) -> str:
    """Return user_id if authenticated, otherwise remote IP address."""
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return f"usr:{user_id}"
    return f"ip:{get_remote_address(request)}"


def get_session_rate_limit_key(request: Request) -> str:
    """Return composite key user_id:session_id for per-session limits."""
    user_id = getattr(request.state, "user_id", None) or get_remote_address(request)
    session_id = request.path_params.get("session_id", "nosession")
    return f"{user_id}:{session_id}"


limiter = Limiter(
    key_func=get_user_or_ip_key,
    default_limits=[DEFAULT_RATELIMIT] if DEFAULT_RATELIMIT else [],
    storage_uri=RATELIMIT_STORAGE_URL,
    enabled=RATELIMIT_ENABLED,
)

