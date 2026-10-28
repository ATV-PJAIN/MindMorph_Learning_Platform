"""Authentication endpoints: Register, Login, and Current User profile."""
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, status

from api.auth_schemas import LoginRequest, RegisterRequest, TokenResponse, UserProfileResponse
from persistence.repository import get_default_user_repository
from services.security import create_access_token, hash_password, verify_password

logger = logging.getLogger(__name__)
auth_router = APIRouter(prefix="/auth", tags=["auth"])


@auth_router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest) -> TokenResponse:
    """Register a new user account and return a signed JWT access token."""
    user_repo = get_default_user_repository()
    existing = user_repo.get_by_email(req.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email address already exists",
        )

    user_id = f"usr_{uuid.uuid4().hex[:16]}"
    hashed_pw = hash_password(req.password)
    user = user_repo.create(
        user_id=user_id,
        email=req.email,
        hashed_password=hashed_pw,
        full_name=req.full_name or "",
    )

    token = create_access_token(user_id=user["id"], email=user["email"])
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=user["id"],
        email=user["email"],
    )


@auth_router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest) -> TokenResponse:
    """Authenticate email and password, returning an access token."""
    user_repo = get_default_user_repository()
    user = user_repo.get_by_email(req.email)
    if not user or not verify_password(req.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(user_id=user["id"], email=user["email"])
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=user["id"],
        email=user["email"],
    )


@auth_router.get("/me", response_model=UserProfileResponse)
def get_me(request: Request) -> UserProfileResponse:
    """Return the currently authenticated user's profile."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_repo = get_default_user_repository()
    user = user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User record not found",
        )

    return UserProfileResponse(
        id=user["id"],
        email=user["email"],
        full_name=user.get("full_name", ""),
        created_at=user.get("created_at"),
    )

