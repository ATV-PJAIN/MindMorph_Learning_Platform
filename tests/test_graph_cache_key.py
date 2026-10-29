import logging

from persistence.repository import _sync_progress_to_neo4j
from services.learner_profile import make_profile
from services.learning_service import _cache_topic


def test_cache_topic_separates_learners_with_different_profiles():
    py = make_profile(primary_language="python", scope="focused")
    js = make_profile(primary_language="javascript", scope="focused")
    assert _cache_topic("Learn  Lists", py) != _cache_topic("learn lists", js)
    assert _cache_topic("Learn  Lists", py) == _cache_topic("learn lists", py)


def test_cache_topic_without_profile_is_the_bare_query():
    assert _cache_topic("  Learn   Lists ", {}) == "learn lists"


def test_neo4j_progress_failure_is_logged_not_raised(monkeypatch, caplog):
    import persistence.private_graph as ng

    def boom(*_a, **_k):
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(ng, "write_session_snapshot", boom)
    with caplog.at_level(logging.WARNING):
        _sync_progress_to_neo4j("s1", {"skill_graph": {"nodes": [{"id": "a"}]}, "node_state": {}}, "u1")
    assert "neo4j progress sync failed" in caplog.text
