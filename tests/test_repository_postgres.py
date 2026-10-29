"""DB-gated integration test for the REAL PostgresLearningSessionRepository.

Skipped automatically when Postgres at config.DATABASE_URL is unreachable (CI without a DB, or a dev
machine with no `docker compose up -d db`), so the default suite stays infra-free. When a DB is up it
exercises the actual JSONB upsert path the in-memory store only mirrors — the one seam that otherwise
returns to zero automated coverage after the manual durability proof.

Run it explicitly after `docker compose up -d db && alembic upgrade head`:
    conda run -n mindmorph python -m pytest tests/test_repository_postgres.py -q
"""
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

import pytest


def _postgres_reachable() -> bool:
    try:
        from sqlalchemy import text
        from persistence.db import get_engine

        with get_engine().connect() as c:
            c.execute(text("select 1"))
        return True
    except Exception:
        if os.getenv("MINDMORPH_REQUIRE_DB_TESTS") == "1":
            raise RuntimeError("Required PostgreSQL tests cannot connect to DATABASE_URL")
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_reachable(),
    reason="Postgres not reachable at DATABASE_URL (run `docker compose up -d db && alembic upgrade head`)",
)


def _ls(summary="v1"):
    return {
        "skill_graph": {"summary": summary, "nodes": [{"id": "a", "label": "A"}], "edges": []},
        "node_state": {"a": {"status": "available", "best_score": 0, "attempts": 0,
                             "weaknesses": [], "last_feedback": None}},
        "lessons": {},
        "selected_node": None,
    }


@pytest.fixture
def repo():
    from persistence.repository import PostgresLearningSessionRepository

    return PostgresLearningSessionRepository()


@pytest.fixture
def user_id():
    # Unique per run so parallel/repeat runs never collide on the shared DB; cleaned up after.
    return f"pytest-{uuid.uuid4().hex[:8]}"


def test_postgres_roundtrip_upsert_list_delete(repo, user_id):
    sid = "s1"
    try:
        # insert
        repo.save(user_id, sid, _ls("v1"), title="Learn X")
        assert repo.get(user_id, sid) == _ls("v1")

        # upsert overwrites data; title preserved when omitted
        updated = repo.get(user_id, sid)
        updated.update(_ls("v2"))
        repo.save(user_id, sid, updated)
        got = repo.get(user_id, sid)
        assert got["skill_graph"]["summary"] == "v2"
        metas = repo.list_sessions(user_id)
        assert len(metas) == 1 and metas[0]["title"] == "Learn X"

        # graded state (the JSONB-serializability concern) round-trips
        graded = repo.get(user_id, sid)
        graded["node_state"]["a"].update(status="mastered", best_score=100,
                                         last_feedback={"score": 100, "passed": 1, "total": 1})
        repo.save(user_id, sid, graded)
        assert repo.get(user_id, sid)["node_state"]["a"]["status"] == "mastered"
    finally:
        repo.delete(user_id, sid)
        assert repo.get(user_id, sid) is None


def test_postgres_rejects_overlapping_updates(repo, user_id):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from persistence.errors import SessionConflictError
    repo.save(user_id, "race", _ls())
    barrier = Barrier(2)
    def update(summary):
        snapshot = repo.get(user_id, "race")
        snapshot["skill_graph"]["summary"] = summary
        barrier.wait(timeout=10)
        try:
            repo.save(user_id, "race", snapshot)
            return "saved"
        except SessionConflictError:
            return "conflict"
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(update, "chat")
            second = pool.submit(update, "grade")
            assert sorted([first.result(), second.result()]) == ["conflict", "saved"]
        with pytest.raises(SessionConflictError):
            repo.save(user_id, "race", _ls("unversioned overwrite"))
    finally:
        repo.delete(user_id, "race")


def test_postgres_state_survives_fresh_api_processes(repo, user_id):
    """Both API reads happen in new interpreters with empty process-local stores."""
    import json
    import subprocess
    from sqlalchemy import text
    from persistence.db import get_engine
    state = _ls("restart")
    state["node_state"]["a"].update(status="mastered", best_score=100, attempts=1)
    state["lessons"] = {"a": {"content": "Saved lesson", "exercise": {"revision": "saved-v1"}}}
    state["assessment"] = {"submitted": True, "results": [{"question_id": "q1", "is_correct": True}]}
    repo.save(user_id, "restart", state)
    code = '''import json, sys
from fastapi.testclient import TestClient
from api.main import app
user = json.load(sys.stdin)
with TestClient(app) as client:
    assert client.get('/ready').status_code == 200
    response = client.get('/sessions/' + user + '/restart')
    assert response.status_code == 200
    print(json.dumps(response.json()['learning_session']))
'''
    try:
        with get_engine().connect() as connection:
            assert connection.execute(text("SELECT data FROM learning_sessions WHERE user_id=:user AND session_id='restart'"), {"user": user_id}).scalar_one() == state
        for _ in range(2):
            result = subprocess.run([sys.executable, "-c", code], input=json.dumps(user_id), text=True,
                                    capture_output=True, timeout=30, cwd=ROOT,
                                    env={**os.environ, "MINDMORPH_STORE": "postgres", "GROQ_API_KEYS": ""})
            assert result.returncode == 0, result.stderr[-2000:]
            assert json.loads(result.stdout.strip().splitlines()[-1]) == state
    finally:
        repo.delete(user_id, "restart")


def test_database_readiness_requires_current_migrations():
    from persistence.db import check_ready
    check_ready()


@pytest.mark.skipif(not os.getenv("MINDMORPH_TEST_DB_CONTAINER"), reason="Outage test requires an explicitly named disposable database container")
def test_real_database_outage_and_recovery(repo, user_id, monkeypatch):
    import json
    import subprocess
    import time
    from fastapi.testclient import TestClient
    from api.main import app
    from persistence.errors import StorageUnavailableError
    from persistence.repository import _memory_singleton
    container = os.environ["MINDMORPH_TEST_DB_CONTAINER"]
    assert container.startswith("mindmorph-remaining-tests-"), "Only disposable test containers may be stopped"
    docker = json.loads(os.getenv("MINDMORPH_TEST_DOCKER_PREFIX", '["docker"]'))
    monkeypatch.setenv("MINDMORPH_STORE", "postgres")
    repo.save(user_id, "outage", _ls("saved before outage"))
    snapshot = repo.get(user_id, "outage")
    snapshot["skill_graph"]["summary"] = "unsaved during outage"
    client = TestClient(app)
    assert client.get("/ready").status_code == 200
    try:
        subprocess.run([*docker, "stop", "--time", "5", container], check=True, capture_output=True, timeout=30)
        try:
            with pytest.raises(StorageUnavailableError):
                repo.save(user_id, "outage", snapshot)
            assert client.get(f"/sessions/{user_id}/outage").status_code == 503
            assert client.get("/ready").status_code == 503
            assert client.get("/health").status_code == 200
            assert _memory_singleton.get(user_id, "outage") is None
        finally:
            subprocess.run([*docker, "start", container], check=True, capture_output=True, timeout=30)
            deadline = time.monotonic() + 20
            while True:
                try:
                    recovered = repo.get(user_id, "outage")
                    break
                except StorageUnavailableError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.2)
        assert recovered["skill_graph"]["summary"] == "saved before outage"
        assert client.get("/ready").status_code == 200
        recovered["skill_graph"]["summary"] = "saved after recovery"
        repo.save(user_id, "outage", recovered)
        assert repo.get(user_id, "outage")["skill_graph"]["summary"] == "saved after recovery"
    finally:
        repo.delete(user_id, "outage")
