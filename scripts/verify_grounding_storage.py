"""Verify #7 on disposable PostgreSQL/Neo4j, preserving the running app's stores.

Run with conda run -n mindmorph python scripts/verify_grounding_storage.py.
Requires Docker (on this Windows workspace, Docker in Ubuntu-22.04 WSL).
"""
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "logs/p1-7-implementation"
OUT.mkdir(parents=True, exist_ok=True)
DOCKER = ["wsl", "-d", "Ubuntu-22.04", "--", "docker"] if os.name == "nt" else ["docker"]
SUFFIX = uuid.uuid4().hex[:8]
PG_NAME = "mindmorph-remaining-tests-p1-7-" + SUFFIX
NEO_NAME = "mindmorph-p1-7-neo-tests-" + SUFFIX
PG_PORT, NEO_PORT = 15437, 17689
PG_URL = f"postgresql+psycopg://grounding:grounding@127.0.0.1:{PG_PORT}/grounding"
PG_DSN = PG_URL.replace("postgresql+psycopg://", "postgresql://")
ENV = {**os.environ, "DATABASE_URL": PG_URL, "MINDMORPH_STORE": "memory",
       "GROQ_API_KEYS": "", "MINDMORPH_OLLAMA_HOST": "http://127.0.0.1:1",
       "MINDMORPH_WEB_RESEARCH": "0", "MINDMORPH_RERANK": "0", "MINDMORPH_RAG": "0",
       "MINDMORPH_RICH_CONTENT": "0", "MINDMORPH_NEO4J_MIRROR": "1",
       "NEO4J_URI": f"bolt://127.0.0.1:{NEO_PORT}", "NEO4J_USER": "neo4j", "NEO4J_PASSWORD": "grounding-tests",
       "KAFKA_BOOTSTRAP_SERVERS": "", "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
       "MINDMORPH_REQUIRE_DB_TESTS": "1", "MINDMORPH_TEST_NEO4J_DISPOSABLE": "1",
       "MINDMORPH_TEST_DB_CONTAINER": PG_NAME, "MINDMORPH_TEST_DOCKER_PREFIX": json.dumps(DOCKER)}
RESULTS = {}


def run(args, *, env=None, log=None, timeout=180):
    result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
    if log:
        (OUT / log).write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"Verification command failed; see {log or args[0]}: {result.stderr[-1200:]}")
    return result.stdout


def ready(function, seconds=60):
    deadline = time.monotonic() + seconds
    while True:
        try:
            return function()
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def migration_matrix():
    import psycopg
    from psycopg import sql
    from psycopg.types.json import Jsonb
    vector = "[" + ",".join(["1"] + ["0"] * 383) + "]"
    for name, revisions in [("cache", ["0003"]), ("revision", ["0003_session_revision"]),
                            ("both", ["0003", "0003_session_revision"]), ("fresh", [])]:
        database = "grounding_" + name
        with psycopg.connect(PG_DSN, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        url = PG_URL.rsplit("/", 1)[0] + "/" + database
        dsn = url.replace("postgresql+psycopg://", "postgresql://")
        env = {**ENV, "DATABASE_URL": url}
        for index, revision in enumerate(revisions):
            run([sys.executable, "-m", "alembic", "upgrade", revision], env=env,
                log=f"migration-{name}-before-{index}.txt")
        if revisions:
            with psycopg.connect(dsn) as connection:
                connection.execute("INSERT INTO learning_sessions (user_id,session_id,data,title) VALUES (%s,%s,%s,%s)",
                                   ("legacy", "saved", Jsonb({"lessons": {"a": {"content": "preserved"}}}), "Legacy"))
                connection.execute("INSERT INTO rag_chunks (user_id,source,content,embedding) VALUES (%s,%s,%s,%s::vector)",
                                   ("legacy", "legacy.pdf", "SQL joins legacy text", vector))
        run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, log=f"migration-{name}-upgrade.txt")
        with psycopg.connect(dsn) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [("0005_grounding_provenance",)]
            if revisions:
                row = connection.execute("SELECT source,content,source_id,page FROM rag_chunks").fetchone()
                assert row == ("legacy.pdf", "SQL joins legacy text", None, None)
                assert connection.execute("SELECT data, revision FROM learning_sessions").fetchone() == ({"lessons": {"a": {"content": "preserved"}}}, 1)
        RESULTS["upgrade_from_" + name] = "passed; legacy data preserved" if revisions else "passed"
    run([sys.executable, "-m", "alembic", "upgrade", "head"], env=ENV, log="migration-main.txt")


def actual_snapshot_checks():
    from neo4j import GraphDatabase
    from persistence.private_graph import write_session_snapshot, graph_key
    from persistence.neo4j_db import close_driver
    from graph.validation import validate_skill_graph
    graph = {"summary": "Private SQL", "nodes": [{"id": "same", "label": "ALICE", "description": "private A", "level": "foundational"},
             {"id": "target", "label": "Joins", "description": "Use joins", "level": "intermediate"}],
             "edges": [{"source": "same", "target": "target"}]}
    write_session_snapshot("alice", "same-session", graph, {"same": {"status": "mastered", "best_score": 99}}, 2)
    other = {**graph, "nodes": [{**node, "label": "BOB", "description": "private B"} for node in graph["nodes"]], "edges": []}
    write_session_snapshot("bob", "same-session", other, {}, 1)
    # A delayed old mirror must not overwrite a newer revision.
    write_session_snapshot("alice", "same-session", other, {}, 1)
    with GraphDatabase.driver(ENV["NEO4J_URI"], auth=("neo4j", "grounding-tests")) as driver:
        with driver.session() as session:
            assert session.run("MATCH (s:SessionSkill {graph_key:$key,id:'same'}) RETURN s.label AS label", key=graph_key("alice", "same-session")).single()["label"] == "ALICE"
            assert session.run("MATCH (s:SessionSkill {graph_key:$key,id:'same'}) RETURN s.label AS label", key=graph_key("bob", "same-session")).single()["label"] == "BOB"
            for user, count in [("alice", 1), ("bob", 0)]:
                assert session.run("MATCH (a:SessionSkill {graph_key:$key})-[:PREREQUISITE]->(b:SessionSkill) RETURN count(b) AS n", key=graph_key(user, "same-session")).single()["n"] == count
            assert session.run("MATCH (s:Skill) RETURN count(s) AS n").single()["n"] == 0
            assert session.run("MATCH (g:LearningGraph {key:$key}) RETURN g.complete AS complete, g.revision AS revision", key=graph_key("alice", "same-session")).single()["revision"] == 2
    # A native-property failure after deleting old owned nodes must roll the whole snapshot back.
    broken = {**graph, "nodes": [{**node, "description": {"unsupported": "map property"}} for node in graph["nodes"]]}
    try:
        write_session_snapshot("alice", "same-session", broken, {}, 3)
    except Exception:
        pass
    else:
        raise AssertionError("Expected a native Neo4j property error")
    with GraphDatabase.driver(ENV["NEO4J_URI"], auth=("neo4j", "grounding-tests")) as driver:
        with driver.session() as session:
            row = session.run("MATCH (g:LearningGraph {key:$key})-[:HAS_SKILL]->(s:SessionSkill {id:'same'}) RETURN g.revision AS revision, g.complete AS complete, s.description AS description", key=graph_key("alice", "same-session")).single()
            assert (row["revision"], row["complete"], row["description"]) == (2, True, "private A")
    close_driver()
    RESULTS["actual_neo4j_snapshot_isolation_stale_revision_and_atomic_rollback"] = "passed"


def capacity():
    import psycopg
    from langchain_core.embeddings import Embeddings
    vector = "[" + ",".join(["1"] + ["0"] * 383) + "]"
    started = time.monotonic()
    with psycopg.connect(PG_DSN) as connection:
        with connection.cursor().copy("COPY rag_chunks (user_id,source,content,embedding,source_id,page) FROM STDIN") as stream:
            for index in range(100_000):
                tenant = index % 10
                stream.write_row((f"bench-{tenant}", "fixture.pdf", f"SQL joins tenant-{tenant} passage {index}", vector, f"doc-{tenant}", 1))
        connection.execute("ANALYZE rag_chunks")
    RESULTS["benchmark_insert_seconds"] = round(time.monotonic() - started, 3)

    class Fixed(Embeddings):
        def embed_documents(self, texts):
            return [[1.0] + [0.0] * 383 for _ in texts]

        def embed_query(self, query):
            return [1.0] + [0.0] * 383

    import rag.embeddings
    from rag.grounding import retrieve_evidence
    from threading import Barrier
    rag.embeddings.get_embeddings = lambda *a, **kw: Fixed()
    # Exercise the complete helper, including tenant filtering, executor admission,
    # deduplication, budgets and metadata. Deterministic vectors isolate storage cost.
    os.environ["MINDMORPH_STORE"] = "postgres"
    barrier = Barrier(10)

    def search(tenant):
        barrier.wait(timeout=15)
        start = time.monotonic()
        result = retrieve_evidence("SQL joins", f"bench-{tenant}")
        assert result["passages"], result["sources"]
        assert all(f"tenant-{tenant} " in row["text"] for row in result["passages"])
        return (time.monotonic() - start) * 1000

    with ThreadPoolExecutor(max_workers=10) as pool:
        durations = list(pool.map(search, range(10)))
    with psycopg.connect(PG_DSN) as connection:
        plan = connection.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT id FROM rag_chunks WHERE user_id='bench-0' ORDER BY embedding <=> %s::vector LIMIT 20", (vector,)).fetchone()[0]
    RESULTS["capacity"] = {"stored_passages": 100_000, "simultaneous_searches": 10,
                           "tenant_passages": 10_000, "all_tenant_isolation_checks": "passed",
                           "p50_ms": round(statistics.median(durations), 2),
                           "p95_ms": round(statistics.quantiles(durations, n=100, method="inclusive")[94], 2),
                           "max_ms": round(max(durations), 2), "durations_ms": [round(d, 2) for d in durations],
                           "scope": "complete helper; deterministic 384-dimensional vectors; web and reranker disabled; not a model-quality benchmark"}
    (OUT / "capacity-query-plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")


def main():
    import psycopg
    from neo4j import GraphDatabase
    started = []
    try:
        run([*DOCKER, "run", "-d", "--name", PG_NAME, "--memory", "512m", "-p", f"127.0.0.1:{PG_PORT}:5432",
             "-e", "POSTGRES_USER=grounding", "-e", "POSTGRES_PASSWORD=grounding", "-e", "POSTGRES_DB=grounding", "pgvector/pgvector:pg16"])
        started.append(PG_NAME)
        print("Disposable PostgreSQL started", flush=True)
        ready(lambda: psycopg.connect(PG_DSN, connect_timeout=2).close())
        migration_matrix()
        print("Four migration upgrade paths passed", flush=True)
        run([*DOCKER, "run", "-d", "--name", NEO_NAME, "--memory", "768m", "-p", f"127.0.0.1:{NEO_PORT}:7687",
             "-e", "NEO4J_AUTH=neo4j/grounding-tests", "-e", "NEO4J_server_memory_heap_initial__size=256m",
             "-e", "NEO4J_server_memory_heap_max__size=256m", "-e", "NEO4J_server_memory_pagecache_size=128m", "neo4j:5-community"])
        started.append(NEO_NAME)

        def neo_ready():
            with GraphDatabase.driver(ENV["NEO4J_URI"], auth=("neo4j", "grounding-tests"), connection_timeout=2) as driver:
                driver.verify_connectivity()
        ready(neo_ready)
        os.environ.update(ENV)
        actual_snapshot_checks()
        print("Neo4j isolation and stale-revision protection passed", flush=True)
        run([sys.executable, "-m", "pytest", "tests/test_repository_postgres.py", "tests/test_rag_pgvector.py", "tests/test_grounding_storage.py",
             "tests/test_completion_gating_neo4j.py", "-q", "--junitxml=logs/p1-7-implementation/storage-tests.xml"],
            env=ENV, log="storage-tests.txt", timeout=240)
        RESULTS["storage_tests"] = "passed; includes restart, outage/recovery and legacy graph tests"
        capacity()
        print(json.dumps(RESULTS, indent=2), flush=True)
    finally:
        for name in reversed(started):
            assert name in (PG_NAME, NEO_NAME) and "tests-" in name
            run([*DOCKER, "rm", "--force", "--volumes", name], timeout=45)
        RESULTS["disposable_containers_removed"] = list(started)
        (OUT / "storage-results.json").write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
