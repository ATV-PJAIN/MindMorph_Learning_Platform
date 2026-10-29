# MindMorph Learning Platform

**Online courses give everyone the same path. MindMorph builds a different one per learner, and won't let you move on until you've proven you understand.**

A multi-agent learning platform built on LangGraph: 16 specialised agents compose into 4 graph workflows that research a topic, draft a personalised skill graph, teach each unit on demand, set exercises, grade your work, and grow the graph around whatever you actually got wrong.

![A learner's exercise scores 0%, locking the failed skill node while the graph grows two new gold remedial prerequisite nodes beside it](docs/media/rewire_hero.gif)

A failing grade locks the node and grows two new prerequisite nodes ahead of it — the graph adapting to what the learner actually got wrong.

Captured footage of the adaptive rewire in action: `docs/media/rewire_demo.mp4` (before/after stills:
`rewire_before.png`, `rewire_after.png`).

[![tests](https://github.com/tarunlnmiit/MindMorph_Learning_Platform/actions/workflows/tests.yml/badge.svg)](https://github.com/tarunlnmiit/MindMorph_Learning_Platform/actions/workflows/tests.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![LangGraph](https://img.shields.io/badge/built%20with-LangGraph-1a1530)](https://langchain-ai.github.io/langgraph/)

---

## How it works

```mermaid
flowchart LR
    A["Learner goal"] --> B["Orchestrator"]
    B --> C["Scout · Academic · Market · Practical<br/>research the topic"]
    C --> D["Consensus · Reviewer<br/>drafts the skill graph"]
    D --> E["lesson_graph<br/>Tutor teaches a unit"]
    E --> F["exercise_graph<br/>Exercise + grading"]
    F --> G{"Mastery<br/>threshold met?"}
    G -->|"score < 40"| H["skill_graph_adapt<br/>inserts remedial nodes"]
    H --> E
    G -->|"score 40-79"| E
    G -->|"score >= 80"| I["Next unit unlocked"]
```

**Progression is gated on demonstrated mastery, not on clicking Next.** `services/mastery.py` scores
each attempt, and the outcome is decided in Python, not the LLM:

- **Score ≥ 80** — mastered. Downstream nodes that depended on it unlock.
- **Score 40–79** — a deliberate no-op. You just retry the node.
- **Score < 40** — the node is locked and the graph grows: an LLM proposes remedial sub-skill nodes,
  inserted as new prerequisites beneath the failed node. Clearing them unlocks it again.

The LLM only ever proposes *what* to add. Adaptation is additive-only — it never renames, reorders, or
deletes an existing node, so a struggling learner never loses progress already made. That invariant is
enforced twice: as a prompt instruction, and mechanically in `apply_adaptation`
(`graph/skill_graph_adapt.py`), which drops edges pointing at unknown ids and refuses to overwrite an
existing node id. If the remediation LLM call fails outright, a `remediation_pending` flag still keeps
the failed node locked — it's never silently left open. Lock state itself isn't stored anywhere; it's
recomputed on every read from the graph plus mastery state (`services/completion.py`), so it can't drift
out of sync.

**Code is submitted in the browser and graded by the backend.** A Monaco editor
(`web/components/CodeEditor.tsx`) collects the submission. `tools/code_executor.py` runs Python tests,
Node.js JavaScript tests, or SQLite SQL assertions with a wall-clock timeout and a secret-scrubbed
environment. Async tests must complete, empty test modules cannot earn credit, and scores come from
validated result files rather than learner stdout. Broken grading artifacts report “not graded” and
leave mastery unchanged. This subprocess is a hang-guard for a local single-user prototype, **not a
security sandbox**; real isolation is a hosted-deployment item. JavaScript grading requires Node.js
20. SQL exercises currently ask the learner to create the required schema/data; verification SELECTs
check the result. Case studies use LLM rubric grading. A separate, optional in-browser JupyterLite scratchpad
(`web/components/Sandbox.tsx`) is for free experimentation only — the graded submission always goes
through the editor above.

## Architecture notes

- **16 agents, one composition layer.** Agents in `agents/` are single-responsibility and know nothing
  about each other; `graph/` wires them into 4 `StateGraph` workflows (learning plan, lesson, content,
  exercise), plus the skill-graph renderer and the additive-merge half of adaptation described above.
  Adding a teaching strategy means adding a graph, not editing an agent.
- **The vector store is deliberately swappable.** Default is `InMemoryVectorStore` with local `fastembed`
  embeddings, so the platform runs with no vector-DB account and no embedding API cost. Set
  `MINDMORPH_STORE=postgres` and the same interface persists to pgvector. `rag/store.py` and
  `rag/pg_store.py` share one contract — callers never change.
- **Runs with no API key; goes fast when you give it one.** `config.get_chat_model` picks the backend
  and `llm_providers.py` owns the failover. With no keys it is a local [Ollama](https://ollama.com)
  install — `qwen2.5:7b` for interactive beats (lesson generation, grading, tutor chat, orchestrator
  routing) and `qwen2.5:14b` for reasoning-heavy, once-per-session work (skill-graph consensus, review,
  scout planning). Set `GROQ_API_KEYS` and hosted [Groq](https://groq.com) inference goes in front of
  it: the keys are a pool, and a rate-limited (429), token-capped (413) or rejected (401) key rotates
  to the next one mid-call. Exhaust the pool and it lands on local Ollama rather than failing. A
  *permanent* error — above all a 404 model-not-found — is not rotated: it logs at ERROR and falls
  back immediately, because every key would fail identically.
- **MCP as a tool transport.** `tools/github_mcp_client.py` talks to MCP servers via
  `langchain-mcp-adapters`, with an explicit timeout wrapper in `tools/mcp_timeout.py` — a hung tool call
  can't stall a graph.
- **Postgres is the system of record when durability is on.** SQLAlchemy 2 models under `persistence/`,
  migrations via Alembic, FastAPI routes in `api/`.
- **Fast local decision routing with Laya (Primary).** Instead of spending 1.5s on a heavy generative LLM
  just to pick one of three routes (`SCOUT` / `CONTENT` / `EXERCISE`), `agents/orchestrator/orchestrator_agent.py`
  runs an in-process **Laya** decision model (`services/routing/laya_router.py`, sub-40ms, zero tokens, Apache 2.0).
  If Laya is missing, loading, or toggled off (`MINDMORPH_USE_LAYA=0`), the orchestrator seamlessly falls back
  to the Groq/Ollama LLM router. See [`docs/LAYA_ROUTING.md`](docs/LAYA_ROUTING.md) for architecture details.
- **Grounded learning and session-owned Neo4j mirrors.** A fourth Knowledge Grounding Agent supplies document/shared/public evidence to roadmap planning. New lessons and practice share a frozen evidence bundle; tutor replies retain sources. Saved sessions enforce prerequisites and mastery, while Neo4j mirrors scoped snapshots. Fresh grounded paths bypass shared roadmap caches and persisted execution checkpoints. See [grounding setup and behavior](docs/GROUNDING.md) and [current graph architecture](docs/NEO4J_ARCHITECTURE.md).
  For local dev, `MINDMORPH_STORE=memory` skips all of that and keeps sessions in memory for the process lifetime.
- **Real-time Kafka Event Streaming & Telemetry Bridge.** Student milestones (`lesson_opened`, `exercise_graded`, `node_mastered`, etc.) are streamed in real time via [`services/kafka_emitter.py`](services/kafka_emitter.py) and traced into LangSmith via [`services/telemetry.py`](services/telemetry.py). Unset `KAFKA_BOOTSTRAP_SERVERS` enables zero-infra dev mode with zero errors. See [`docs/KAFKA_TELEMETRY.md`](docs/KAFKA_TELEMETRY.md) for architecture and event schemas.

## Run it in two minutes

**No API key required.** Out of the box the LLM is a local Ollama model — no vendor account, no key, no
infra beyond Ollama itself, no Docker, no Postgres, no Alembic, no build step to start. Everything
below works exactly as written with zero credentials.

```bash
git clone https://github.com/tarunlnmiit/MindMorph_Learning_Platform.git
cd MindMorph_Learning_Platform

conda create -n mindmorph python=3.11 -y && conda activate mindmorph   # or: python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Install Ollama (https://ollama.com/download), then pull both tier models
ollama pull qwen2.5:7b
ollama pull qwen2.5:14b

# Backend — zero-infra in-memory store
MINDMORPH_STORE=memory conda run -n mindmorph uvicorn api.main:app --port 8000

# Frontend, in another terminal
cd web && npm install && npm run dev
```

Opens at `http://localhost:3000`. Sessions live in memory for the process lifetime; see below for the
durable Postgres/RAG setup.

### Optional: hosted inference, for when local is too slow

Local inference is free but slow — a full skill-graph build takes minutes on `qwen2.5:14b`. If you want
speed, put [Groq](https://console.groq.com/keys) in front of it. It stays optional: remove the keys and
you are back on Ollama with no other change.

```bash
# .env — one or more free-tier keys, comma-separated. Rotation multiplies the per-key quota.
GROQ_API_KEYS=gsk_yourfirstkey,gsk_yoursecondkey,gsk_yourthirdkey
```

That's the whole setup — the provider defaults to `groq` as soon as keys are present, and to `ollama`
when they aren't. Measured on Groq via `scripts/live_smoke.py`: orchestrator routing `0.4–0.8s`, a
seven-node skill-graph consensus build `2.8s`, first tutor token `1.0s`.

Free-tier quota is metered **per key, per model** (for `openai/gpt-oss-120b`: 30 requests/min, 1K
requests/day, 8K tokens/min, 200K tokens/day), which is why the pool exists — five keys is roughly 5x
the headroom. Keep the model on the OpenAI OSS family; Groq has deprecated its Llama models.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEYS` | unset | Comma-separated Groq key pool. Unset → local Ollama only, no key needed |
| `MINDMORPH_LLM_PROVIDER` | `groq` if keys are set, else `ollama` | Primary backend |
| `MINDMORPH_LLM_FALLBACK` | `ollama` | Where a Groq call lands when the pool is exhausted or misconfigured |
| `MINDMORPH_GROQ_MODEL` | `openai/gpt-oss-120b` | Fast tier when Groq is active |
| `MINDMORPH_GROQ_MODEL_COMPLEX` | `openai/gpt-oss-120b` | Quality tier when Groq is active |
| `MINDMORPH_OLLAMA_MODEL` | `qwen2.5:7b` | Fast tier — interactive beats |
| `MINDMORPH_OLLAMA_MODEL_COMPLEX` | `qwen2.5:14b` | Quality tier — reasoning-heavy, once-per-session work |
| `MINDMORPH_OLLAMA_HOST` | `http://localhost:11434` | Ollama endpoint |
| `MINDMORPH_STORE` | `postgres` | Set to `memory` for zero-infra dev (as the quickstart does); the unset default is the durable Postgres store |
| `DATABASE_URL` | `postgresql+psycopg://mindmorph:mindmorph@localhost:5432/mindmorph` | Matches `docker-compose.yml`, used when `MINDMORPH_STORE=postgres` |
| `NEO4J_URI` | `bolt://localhost:7687` | Connection string for Neo4j Graph DB |
| `MINDMORPH_RAG` | off | Enables shared seed retrieval; personal uploads remain independently eligible |
| `MINDMORPH_KNOWLEDGE_DIR` | `knowledge_base/` | Shared corpus loaded lazily |
| `MINDMORPH_WEB_RESEARCH` | on | Common bounded public research for grounding |
| `MINDMORPH_RERANK` | on | Local MiniLM passage ranking, with an explicit degraded fallback |
| `MINDMORPH_MODEL_CACHE` | optional | Common local embedding/ranking model cache directory |
| `MINDMORPH_NEO4J_MIRROR` | on | Session-owned graph snapshots; session-derived locks work when disabled |
| `MINDMORPH_USE_LAYA` | `1` | Enables fast local Laya decision router (1 = on, 0 = force LLM fallback) |
| `MINDMORPH_LAYA_MODEL` | `convaiinnovations/laya` | Model identifier or local weights path for Laya |
| `KAFKA_BOOTSTRAP_SERVERS` | unset | Comma-separated Kafka brokers (e.g. `localhost:9092`). Unset enables safe zero-infra dev mode |
| `MINDMORPH_KAFKA_TOPIC` | `mindmorph.learning_events` | Kafka topic for real-time learner event streaming |
| `NEXT_PUBLIC_JUPYTERLITE_URL` | hosted JupyterLite demo | Overrides the in-lesson scratchpad REPL |

For the durable path: `docker compose up -d db && conda run -n mindmorph alembic upgrade head`, then
start the backend without `MINDMORPH_STORE=memory`.
Run `alembic upgrade head` after pulling schema changes, including `0003_session_revision`.
The API does not auto-create or migrate tables. `/health` checks the running process; `/ready`
checks database connectivity and migration state (memory mode reports `durable: false`).
Database errors return 503; overlapping stale saves return 409 and require reload/retry.

### Observability (LangSmith)

To trace the execution of the 16 agents and 4 graphs (highly recommended for debugging AI reasoning and adaptation loops), you can use LangSmith. 

**Setup Instructions:**
1. Go to [smith.langchain.com](https://smith.langchain.com/) and create a free account.
2. In the LangSmith UI, click on **Tracing** and create a new project named `MindMorph_Local`.
3. Go to Settings (gear icon) -> API Keys, and generate a new API Key.
4. Add the following to your `.env` file, pasting your new key:

```env
LANGCHAIN_TRACING_V2=true
LANGCHAIN_ENDPOINT="https://api.smith.langchain.com"
LANGCHAIN_PROJECT="MindMorph_Local"
LANGCHAIN_API_KEY="your_langsmith_api_key"
```

5. **Restart your backend:** Kill the running `uvicorn` process in your terminal and start it again so it loads the new keys.
6. **Trigger a run:** Go to the web UI and search for a new topic. Return to your LangSmith project dashboard to see the real-time visual trace tree:
   - Under the `orchestrator` node, see the explicit `laya_local_router` span indicating sub-40ms zero-token routing.
   - For every learning action (`lesson_opened`, `exercise_graded`, `node_mastered`), see the child `learning_event_telemetry` and `kafka_event_emitter` spans tracking full payload metadata and emission delivery status.

### Programmatic access

```bash
uvicorn api.main:app --reload      # FastAPI routes in api/routes.py
```

## Tests

```bash
MINDMORPH_STORE=memory conda run -n mindmorph python -m pytest tests/ -q
```

## Project layout

| Path | What lives there |
|---|---|
| `agents/` | 16 single-responsibility agents — orchestrator, scout, academic, market, practical, consensus, reviewer, tutor, assessment, exercise, adaptation, content_generator, example_generator, visual_generator, synthesizer, factual |
| `graph/` | 4 LangGraph workflows composing those agents, plus the skill-graph renderer and the adaptation merge |
| `rag/` | Embeddings, chunking, in-memory and pgvector stores |
| `services/` | Mastery scoring, completion, learning-service orchestration, telemetry bridge (`telemetry.py`, `kafka_emitter.py`, `events.py`) |
| `services/routing/` | Fast local decision routing (`laya_router.py` primary + LLM fallback) |
| `tools/` | Timeout-guarded code executor, MCP client, scrapers |
| `api/` | FastAPI layer |
| `persistence/` | SQLAlchemy models |
| `web/` | Next.js frontend — Monaco editor, JupyterLite scratchpad |

## Status

Actively developed, single-user, no auth, no deployment story yet. `docs/ARCHITECTURE.md` describes a
larger target architecture (Kafka, Kubernetes, Pinecone, multi-tenant) than what currently ships — treat
that document as the roadmap, `docs/IMPLEMENTATION_STATUS.md` as what's actually built, and this README
as what runs today.

## License

MIT — see [LICENSE](LICENSE).

---

*I turn scattered AI capabilities into tools people can actually run. · Available for AI contract work → [github.com/tarunlnmiit](https://github.com/tarunlnmiit)*
