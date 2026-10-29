from unittest.mock import MagicMock

import pytest
from persistence import private_graph
from services.completion import locked_node_ids


GRAPH = {"nodes": [{"id": "a", "label": "First", "level": "foundational"},
                   {"id": "b", "label": "Second", "level": "intermediate"}],
         "edges": [{"source": "a", "target": "b"}]}


def test_session_locks_ignore_even_empty_or_failed_neo4j(monkeypatch):
    monkeypatch.setattr("persistence.neo4j_graph.get_completed_and_locked_nodes",
                        MagicMock(side_effect=AssertionError("must not consult mirror")))
    graph = {**GRAPH, "session_id": "nested-id"}
    assert locked_node_ids(graph, {"a": {"status": "available"}, "b": {"status": "mastered"}}) == {"b"}


def test_snapshot_is_scoped_and_published_after_all_transaction_writes(monkeypatch):
    monkeypatch.setenv("MINDMORPH_NEO4J_MIRROR", "1")
    monkeypatch.setattr(private_graph, "ensure_schema", lambda: None)
    driver = MagicMock()
    tx = MagicMock()
    tx.run.return_value.single.return_value = {"key": "present"}
    driver.session.return_value.__enter__.return_value.execute_write.side_effect = lambda function: function(tx)
    monkeypatch.setattr(private_graph, "get_driver", lambda: driver)
    private_graph.write_session_snapshot("alice", "same", GRAPH, {}, 3)
    calls = tx.run.call_args_list
    key = private_graph.graph_key("alice", "same")
    assert key != private_graph.graph_key("bob", "same")
    assert all(call.kwargs["key"] == key for call in calls)
    assert "complete = true" in calls[-1].args[0]
    assert all(":Skill " not in call.args[0] for call in calls)
    assert any("graph_key: $key, id: e.target" in call.args[0] for call in calls)


def test_delayed_older_revision_does_not_replace_newer_snapshot(monkeypatch):
    monkeypatch.setenv("MINDMORPH_NEO4J_MIRROR", "1")
    monkeypatch.setattr(private_graph, "ensure_schema", lambda: None)
    driver, tx = MagicMock(), MagicMock()
    tx.run.return_value.single.return_value = None  # locked revision check rejects old write
    driver.session.return_value.__enter__.return_value.execute_write.side_effect = lambda function: function(tx)
    monkeypatch.setattr(private_graph, "get_driver", lambda: driver)
    private_graph.write_session_snapshot("alice", "same", GRAPH, {}, 1)
    assert tx.run.call_count == 1
