"""Atomic session-owned Neo4j mirrors; legacy shared Topic/Skill data stays untouched."""
from functools import lru_cache
import hashlib
import os

from persistence.neo4j_db import get_driver


def graph_key(user_id: str, session_id: str) -> str:
    return hashlib.sha256((str(len(user_id)) + ":" + user_id + session_id).encode()).hexdigest()


@lru_cache(maxsize=1)
def ensure_schema():
    with get_driver().session() as session:
        session.run("CREATE CONSTRAINT private_learning_graph IF NOT EXISTS "
                    "FOR (g:LearningGraph) REQUIRE g.key IS UNIQUE").consume()
        session.run("CREATE CONSTRAINT private_learning_skill IF NOT EXISTS "
                    "FOR (s:SessionSkill) REQUIRE (s.graph_key, s.id) IS UNIQUE").consume()


def write_session_snapshot(user_id: str, session_id: str, skill_graph: dict,
                           node_state: dict, revision: int) -> None:
    if os.getenv("MINDMORPH_NEO4J_MIRROR", "1") == "0":
        return
    from graph.validation import validate_skill_graph
    validate_skill_graph(skill_graph)
    ensure_schema()
    key = graph_key(user_id, session_id)

    def write(tx):
        # This write obtains the per-graph lock before comparing revisions. A delayed
        # older mirror cannot replace a newer committed session snapshot.
        row = tx.run("MERGE (g:LearningGraph {key: $key}) "
                     "SET g.write_lock = coalesce(g.write_lock, 0) + 1 "
                     "WITH g WHERE coalesce(g.revision, 0) <= $revision "
                     "SET g.complete = false RETURN g.key AS key", key=key, revision=revision).single()
        if row is None:
            return
        tx.run("MATCH (g:LearningGraph {key: $key}) "
               "OPTIONAL MATCH (g)-[:HAS_SKILL|HAS_PROGRESS]->(owned) "
               "DETACH DELETE owned", key=key).consume()
        nodes = [{**node, "status": node_state.get(node["id"], {}).get("status", "available"),
                  "best_score": node_state.get(node["id"], {}).get("best_score", 0),
                  "pending": node_state.get(node["id"], {}).get("remediation_pending", False)}
                 for node in skill_graph.get("nodes", [])]
        tx.run("MATCH (g:LearningGraph {key: $key}) UNWIND $nodes AS n "
               "CREATE (s:SessionSkill {graph_key: $key, id: n.id, label: n.label, "
               "description: coalesce(n.description, ''), level: n.level}) "
               "CREATE (g)-[:HAS_SKILL]->(s) "
               "CREATE (p:SessionProgress {graph_key: $key, node_id: n.id, "
               "status: n.status, best_score: n.best_score, remediation_pending: n.pending}) "
               "CREATE (g)-[:HAS_PROGRESS]->(p)-[:FOR_SKILL]->(s)", key=key, nodes=nodes).consume()
        tx.run("UNWIND $edges AS e MATCH (a:SessionSkill {graph_key: $key, id: e.source}), "
               "(b:SessionSkill {graph_key: $key, id: e.target}) "
               "MERGE (a)-[:PREREQUISITE]->(b)", key=key, edges=skill_graph.get("edges", [])).consume()
        tx.run("MATCH (g:LearningGraph {key: $key}) "
               "SET g.summary = $summary, g.revision = $revision, g.complete = true",
               key=key, summary=skill_graph.get("summary", ""), revision=revision).consume()

    with get_driver().session() as session:
        session.execute_write(write)
