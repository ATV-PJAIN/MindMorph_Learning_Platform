# Neo4j Graph Architecture — MindMorph

> **Current P1 #7 behavior:** saved sessions are authoritative for prerequisites, mastery and
> completion. Neo4j is a session-owned mirror, not the completion engine or a lesson bank.
> Grounded paths bypass the shared roadmap caches and disable persisted execution checkpoints.
> See [GROUNDING.md](GROUNDING.md). The original phase notes below describe the legacy design.

## Current session-owned representation

`persistence/private_graph.py` writes `LearningGraph` → `SessionSkill` and `SessionProgress`
snapshots after successful session saves. A graph key derives from caller/session identity;
skills are unique within that graph. Transactions replace only owned nodes/edges, reject stale
revisions under a write lock, and publish completeness last. Failure leaves the prior snapshot
intact and cannot change session-derived locks. Old `Topic`/`Skill` data is preserved.

Inspect the new representation using the locally configured Neo4j Browser and credentials:

```cypher
MATCH (g:LearningGraph)-[:HAS_SKILL]->(s:SessionSkill)
OPTIONAL MATCH (s)-[r:PREREQUISITE]->(next:SessionSkill {graph_key: s.graph_key})
RETURN g, s, r, next
```

```cypher
MATCH (g:LearningGraph)-[:HAS_PROGRESS]->(p:SessionProgress)-[:FOR_SKILL]->(s:SessionSkill)
RETURN g.key AS graph, g.revision AS revision, g.complete AS complete,
       s.id AS skill, p.status AS status, p.best_score AS score
```

## Legacy phase notes

MindMorph has successfully migrated its backend data layer from a JSONB Postgres cache into a fully Graph-Native architecture powered by Neo4j. This document outlines what was implemented, how the system works under the hood, and how developers can test the graph behavior directly using Cypher queries.

## What Was Implemented

1. **Hybrid Caching System**
   - **Postgres Fallback**: A `graph_cache` table handles high-speed lookups of previously generated topics to save LLM tokens.
   - **Neo4j Canonical Graph**: The extracted learning paths (Topics, Skills, and Prerequisites) are written natively as nodes and edges into Neo4j using the `MERGE` command.

2. **Graph-Native Completion Engine**
   - We entirely replaced the in-memory Python Depth-First Search (DFS) for lesson locking and completion.
   - The backend now computes what lessons are available, mastered, and locked dynamically via lightning-fast Cypher `MATCH` queries over the graph.

3. **User Progress Tracking**
   - User session progress is mapped to a `(:Session)` node.
   - For every lesson, a `(:Progress)` node tracks the score and mastery status, which links directly to the canonical `(:Skill)` node in the knowledge graph.

4. **Dynamic AI Remediation (The Adaptation Loop)**
   - When a user fails a lesson, the LangGraph AI dynamically proposes a remedial sub-graph.
   - These new nodes and edges are instantly saved to the Neo4j database under the original topic ID, seamlessly injecting new content into the user's graph.

5. **LangGraph Neo4j Checkpointer**
   - The entire multi-agent state execution is backed by the `langchain-neo4j` checkpointer, meaning you can traverse AI thoughts and executions as graph nodes.

---

## Setup Guide

To run the full MindMorph architecture locally, ensure you have Docker and your Python environment running.

1. **Start the Infrastructure:**
   ```bash
   # Starts Postgres (port 5433) and Neo4j (ports 7474, 7687)
   docker compose up -d
   ```

2. **Run Migrations:**
   ```bash
   conda run -n mindmorph alembic upgrade head
   ```

3. **Start the API and Web App:**
   ```bash
   conda run -n mindmorph uvicorn api.main:app --port 8000 --reload
   # In a separate terminal:
   cd web && npm run dev
   ```

---

## Testing & Verification using Neo4j Browser

You can directly inspect how MindMorph builds your knowledge graph using the Neo4j Browser.

**Access the Browser:** 
Navigate to `http://localhost:7474`
- Username: `neo4j`
- Password: use the credentials configured in your local `.env`.

### 1. View a Full Topic Graph
After searching for a topic (e.g., "Python Basics") in the UI, run this to see the generated knowledge graph:
```cypher
MATCH (t:Topic)-[:HAS_SKILL]->(s:Skill)
OPTIONAL MATCH (s)-[r:PREREQUISITE]->(target:Skill)
RETURN t, s, r, target
```

### 2. Check User Mastery & Progress
Want to see how your specific session is scoring on lessons? Run this to see your Session node and its attached Progress scores:
```cypher
MATCH (sess:Session)-[:HAS_PROGRESS]->(p:Progress)-[:FOR_SKILL]->(s:Skill)
RETURN sess.id AS Session_ID, s.label AS Lesson, p.status AS Status
```

### 3. Test Graph Adaptation (Remediation)
If you intentionally fail a lesson in the UI (score < 40), the AI will generate remedial lessons. You can verify they attached correctly to your graph by looking for the `remediation_pending` flag or just re-running the Full Topic Graph query from Step 1.

### 4. Understand the Cypher Completion Engine
This is the exact query our backend uses to figure out if you have fully "Mastered" a lesson. A lesson is only mastered if you passed it, AND you passed every single prerequisite behind it!
```cypher
MATCH (s:Session)-[:HAS_PROGRESS]->(p:Progress)-[:FOR_SKILL]->(n:Skill)
WHERE p.status = 'mastered'
// Find if there is ANY unmastered prerequisite blocking this node...
OPTIONAL MATCH (unmastered_p:Progress)-[:FOR_SKILL]->(unmastered:Skill)-[:PREREQUISITE*1..]->(n)
WHERE unmastered_p.session_id = s.id AND unmastered_p.status <> 'mastered'
WITH n, count(unmastered) AS incomplete_prereqs
WHERE incomplete_prereqs = 0
RETURN n.id AS Fully_Mastered_Lesson
```
