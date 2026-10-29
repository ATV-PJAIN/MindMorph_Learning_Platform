"""Repository contract — round-trip, upsert, listing, isolation. Runs against the in-memory store
(zero infra); the Postgres impl satisfies the same interface and is exercised live via the verification
steps (docker compose up -d db). The same assertions hold for any LearningSessionRepository."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

import pytest
from datetime import datetime, timezone

from persistence.repository import InMemoryLearningSessionRepository


def _ls(summary="v1"):
    return {
        "skill_graph": {"summary": summary, "nodes": [{"id": "a"}], "edges": []},
        "node_state": {"a": {"status": "available", "best_score": 0, "attempts": 0,
                             "weaknesses": [], "last_feedback": None}},
        "lessons": {},
        "selected_node": None,
    }


@pytest.fixture
def repo():
    return InMemoryLearningSessionRepository()


def test_save_then_get_roundtrips_deep_equal(repo):
    ls = _ls()
    repo.save("u1", "s1", ls, title="Learn X")
    assert repo.get("u1", "s1") == ls


def test_get_missing_returns_none(repo):
    assert repo.get("nobody", "nope") is None


def test_save_upserts_data(repo):
    repo.save("u1", "s1", _ls("v1"), title="Learn X")
    updated = repo.get("u1", "s1")
    updated.update(_ls("v2"))
    repo.save("u1", "s1", updated)  # overwrite, no title
    got = repo.get("u1", "s1")
    assert got["skill_graph"]["summary"] == "v2"


def test_upsert_without_title_preserves_existing_title(repo):
    repo.save("u1", "s1", _ls(), title="Original")
    updated = repo.get("u1", "s1")
    updated.update(_ls("v2"))
    repo.save("u1", "s1", updated)  # no title supplied
    meta = repo.list_sessions("u1")
    assert meta[0]["title"] == "Original"


def test_node_state_survives_simulated_restart(repo):
    # Mutate node_state (a grade), persist, then read back through a fresh fetch — the new state must
    # be the one stored, proving progress is durable across a load cycle.
    ls = _ls()
    ls["node_state"]["a"]["status"] = "mastered"
    ls["node_state"]["a"]["best_score"] = 100
    repo.save("u1", "s1", ls)
    reloaded = repo.get("u1", "s1")
    assert reloaded["node_state"]["a"]["status"] == "mastered"
    assert reloaded["node_state"]["a"]["best_score"] == 100


def test_stored_state_is_isolated_from_caller_mutation(repo):
    # The store deep-copies (matching the JSONB serialize boundary): mutating the saved dict, or the
    # returned one, must not change what's persisted.
    ls = _ls()
    repo.save("u1", "s1", ls)
    ls["node_state"]["a"]["status"] = "tampered"      # mutate the original after save
    got = repo.get("u1", "s1")
    got["node_state"]["a"]["status"] = "also-tampered"  # mutate the returned copy
    assert repo.get("u1", "s1")["node_state"]["a"]["status"] == "available"


def test_list_sessions_scoped_per_user_recent_first(repo):
    repo.save("u1", "s1", _ls(), title="first")
    repo.save("u1", "s2", _ls(), title="second")
    repo.save("u2", "s3", _ls(), title="other-user")
    metas = repo.list_sessions("u1")
    assert {m["session_id"] for m in metas} == {"s1", "s2"}
    assert metas[0]["session_id"] == "s2"  # most-recently-saved first


def test_list_sessions_order_is_deterministic_when_timestamps_tie(repo, monkeypatch):
    from persistence import repository as repository_module

    fixed = datetime(2025, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(repository_module, "datetime", type("FixedDateTime", (), {
        "now": staticmethod(lambda _tz=None: fixed),
    }))
    repo.save("u1", "s1", _ls())
    repo.save("u1", "s2", _ls())
    assert [m["session_id"] for m in repo.list_sessions("u1")] == ["s2", "s1"]
    updated = repo.get("u1", "s1")
    updated.update(_ls("resaved"))
    repo.save("u1", "s1", updated)
    assert [m["session_id"] for m in repo.list_sessions("u1")] == ["s1", "s2"]
    assert all(m["updated_at"] == fixed.isoformat() for m in repo.list_sessions("u1"))


def test_delete_removes_session(repo):
    repo.save("u1", "s1", _ls())
    repo.delete("u1", "s1")
    assert repo.get("u1", "s1") is None
    repo.delete("u1", "s1")  # idempotent — no error
