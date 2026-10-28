"""Comprehensive test suite for JWT Authentication, Middleware, and Rate Limiting."""
import os
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from api.main import app
from persistence.repository import InMemoryUserRepository, get_default_repository, get_default_user_repository
from services.security import create_access_token, hash_password, verify_password


@pytest.fixture(autouse=True)
def setup_memory_store(monkeypatch):
    monkeypatch.setenv("MINDMORPH_STORE", "memory")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-for-testing-12345")
    # Reset in-memory repos
    user_repo = get_default_user_repository()
    if isinstance(user_repo, InMemoryUserRepository):
        user_repo._by_id.clear()
        user_repo._by_email.clear()
    session_repo = get_default_repository()
    if hasattr(session_repo, "_store"):
        session_repo._store.clear()


def test_password_hashing_and_verification():
    raw = "MySecurePassword123!"
    hashed = hash_password(raw)
    assert hashed != raw
    assert verify_password(raw, hashed)
    assert not verify_password("WrongPassword", hashed)


def test_jwt_token_creation_and_decoding():
    token = create_access_token("usr_123", "test@mindmorph.ai")
    assert isinstance(token, str)
    from services.security import decode_access_token
    claims = decode_access_token(token)
    assert claims is not None
    assert claims["sub"] == "usr_123"
    assert claims["email"] == "test@mindmorph.ai"


def test_auth_registration_and_login_flow():
    client = TestClient(app)

    # 1. Register new user
    reg_resp = client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "supersecretpassword", "full_name": "Alice Wonderland"},
    )
    assert reg_resp.status_code == 201
    data = reg_resp.json()
    assert data["email"] == "alice@example.com"
    token = data["access_token"]
    user_id = data["user_id"]

    # 2. Duplicate registration rejected
    dup_resp = client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "supersecretpassword"},
    )
    assert dup_resp.status_code == 400

    # 3. Login with correct password
    login_resp = client.post(
        "/auth/login",
        json={"email": "alice@example.com", "password": "supersecretpassword"},
    )
    assert login_resp.status_code == 200
    assert "access_token" in login_resp.json()

    # 4. Login with invalid password
    bad_login = client.post(
        "/auth/login",
        json={"email": "alice@example.com", "password": "wrongpassword123"},
    )
    assert bad_login.status_code == 401

    # 5. Access /auth/me with Bearer token
    me_resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["id"] == user_id
    assert me_data["email"] == "alice@example.com"


def test_middleware_blocks_unauthenticated_protected_routes():
    client = TestClient(app)

    # Health check is public
    health = client.get("/health")
    assert health.status_code == 200

    # Protected sessions route without token -> 401
    resp = client.get("/sessions/usr_random")
    assert resp.status_code == 401
    assert "detail" in resp.json()

    # Protected sessions route with garbage token -> 401
    resp_bad = client.get("/sessions/usr_random", headers={"Authorization": "Bearer invalid.token.payload"})
    assert resp_bad.status_code == 401


def test_multi_tenant_isolation_returns_403():
    client = TestClient(app)

    # Register User A
    resp_a = client.post(
        "/auth/register",
        json={"email": "userA@example.com", "password": "password1234"},
    )
    token_a = resp_a.json()["access_token"]
    user_a = resp_a.json()["user_id"]

    # Register User B
    resp_b = client.post(
        "/auth/register",
        json={"email": "userB@example.com", "password": "password1234"},
    )
    token_b = resp_b.json()["access_token"]
    user_b = resp_b.json()["user_id"]

    # User B attempts to list User A's sessions -> 403 Forbidden
    cross_list = client.get(f"/sessions/{user_a}", headers={"Authorization": f"Bearer {token_b}"})
    assert cross_list.status_code == 403
    assert "Forbidden" in cross_list.json()["detail"]


def test_rate_limiting_enforced():
    client = TestClient(app)
    reg = client.post(
        "/auth/register",
        json={"email": "ratelimit@example.com", "password": "password1234"},
    )
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    user_id = reg.json()["user_id"]

    with patch("persistence.neo4j_graph.write_session_progress"):
        # Test rate limiting on /sessions/{user_id}/assessment which has a 15/minute limit
        # Or test hitting 429 when threshold exceeded
        responses = []
        for _ in range(35):
            r = client.post(
                f"/sessions/{user_id}/session123/lessons/node_test",
                headers=headers,
            )
            responses.append(r.status_code)

        assert 429 in responses
