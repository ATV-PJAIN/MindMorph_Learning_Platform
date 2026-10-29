import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

import pytest
from persistence.neo4j_graph import write_session_progress, get_completed_and_locked_nodes
from persistence.neo4j_db import get_driver

pytestmark = pytest.mark.skipif(
    os.getenv("MINDMORPH_TEST_NEO4J_DISPOSABLE") != "1",
    reason="Legacy graph tests require an explicitly disposable Neo4j database",
)

@pytest.fixture(autouse=True)
def clean_neo4j():
    """Clean the Neo4j database before and after each test."""
    def _clean():
        try:
            with get_driver().session() as session:
                session.run("MATCH (n) DETACH DELETE n")
        except Exception:
            pass
    _clean()
    yield
    _clean()


def setup_graph_and_progress(session_id: str, edges: list, statuses: dict, pending_remediation: list = None):
    """Helper to setup nodes, edges, and progress in Neo4j."""
    if pending_remediation is None:
        pending_remediation = []
        
    with get_driver().session() as session:
        # Create topic
        session.run("MERGE (t:Topic {canonical: 'test_topic'}) SET t.graph_built = true")
        
        # Create nodes based on statuses keys
        for nid in statuses.keys():
            session.run("MERGE (s:Skill {id: $id})", id=nid)
            session.run(
                "MATCH (t:Topic {canonical: 'test_topic'}), (s:Skill {id: $id}) "
                "MERGE (t)-[:HAS_SKILL]->(s)", id=nid
            )
            
        # Create edges
        for edge in edges:
            session.run(
                "MATCH (a:Skill {id: $src}), (b:Skill {id: $tgt}) "
                "MERGE (a)-[:PREREQUISITE]->(b)",
                src=edge["source"], tgt=edge["target"]
            )
            
    # Write progress using our new helper
    node_state = {}
    for nid, status in statuses.items():
        node_state[nid] = {
            "status": status,
            "remediation_pending": nid in pending_remediation
        }
    write_session_progress(session_id, node_state)


def test_neo4j_mastered_with_unmastered_prereq_is_locked():
    session_id = "test_session_1"
    setup_graph_and_progress(
        session_id=session_id,
        edges=[{"source": "a", "target": "b"}],
        statuses={"a": "available", "b": "mastered"}
    )
    
    completed, locked = get_completed_and_locked_nodes(session_id)
    
    # b passed its exercise but its prereq 'a' is not mastered
    assert "b" not in completed
    assert "a" not in completed
    assert "b" in locked


def test_neo4j_chain_completes_when_all_mastered():
    session_id = "test_session_2"
    setup_graph_and_progress(
        session_id=session_id,
        edges=[{"source": "a", "target": "b"}, {"source": "b", "target": "c"}],
        statuses={"a": "mastered", "b": "mastered", "c": "mastered"}
    )
    
    completed, locked = get_completed_and_locked_nodes(session_id)
    
    assert completed == {"a", "b", "c"}
    assert locked == set()


def test_neo4j_partial_chain_gates_downstream():
    session_id = "test_session_3"
    setup_graph_and_progress(
        session_id=session_id,
        edges=[{"source": "a", "target": "b"}, {"source": "b", "target": "c"}],
        statuses={"a": "mastered", "b": "available", "c": "mastered"}
    )
    
    completed, locked = get_completed_and_locked_nodes(session_id)
    
    assert completed == {"a"}
    assert "b" not in completed
    assert "c" not in completed
    # 'b' has a completed prereq ('a'), but is not itself completed
    # wait, 'b' is not locked because its prereq 'a' IS completed!
    # 'c' IS locked because its prereq 'b' is NOT completed!
    assert "c" in locked


def test_neo4j_remediation_pending_locks_node():
    session_id = "test_session_4"
    setup_graph_and_progress(
        session_id=session_id,
        edges=[], # No prereqs yet
        statuses={"a": "needs_review", "b": "available"},
        pending_remediation=["a"]
    )
    
    completed, locked = get_completed_and_locked_nodes(session_id)
    
    assert "a" not in completed
    assert "a" in locked # Deterministically locked due to remediation_pending
