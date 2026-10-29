# MindMorph — Implementation Status & Roadmap

> Companion to [`ARCHITECTURE.md`](./ARCHITECTURE.md) (the target design).
> This document maps the **target architecture** to **what the code actually does today**,
> then lays out a prioritized roadmap.
> **Naming:** product = **MindMorph** (the source PDFs called it "SmartLearn" — older name; no live
> discrepancy).

**Status legend:** ✅ Done · 🟡 Partial / stub · ⛔ Planned (not in code)

**Grading repair verified 16 October 2026:** Python, Node.js JavaScript and SQLite SQL execution
are implemented. Windows and Ubuntu backend verification: **385 passed, 3 real-database checks skipped on each**;
Final runner regressions: **93 passed on each platform**, including path names with spaces.
Playwright: **44 passed**; type checking and a clean production build passed. The working-tree
repair was committed as `b57c43b` on `feature/learning-flow-improvements`. These checks do not
verify a deployment. Detailed audit and work records are kept as local-only documents.

**Reliability repair - 21 October 2026:** PostgreSQL no longer falls back silently to memory.
Session writes compare a revision (migration `0003_session_revision`); stale updates return a conflict.
`/health` reports process liveness; `/ready` checks database connectivity and migration head, and labels
memory mode as non-durable. Database migrations are explicit (`python -m alembic upgrade head`), not
created at API startup. Real-database tests cover concurrent writes, fresh API processes and outage/recovery.
Assessment retries preserve mastery; question IDs and answer bounds are checked. Initial roadmaps and
adaptations reject structural defects; grading rechecks locks and exercise revisions. A validated learner
profile reaches planning, content, examples, practice, assessment and tutor messages. Non-coding domains
avoid coding-only research sources. Dialog accessibility, skip/failure recovery and persisted assessment
review are implemented. **Verification: 415 backend tests passed with zero skips (real database enabled); 50 browser tests
passed; type checking and a clean isolated production build passed.** Two approved live hosted samples
(JavaScript/browser widget and guitar/song) passed follow-up generation and produced valid seven-skill
roadmaps; the guitar path had no coding skills. Research perspectives were synthetic supplied inputs.
Comprehensive live lesson/exercise quality, a fresh Ubuntu run and public deployment remain unverified.

---

## 1. Current Architecture vs Target

| | Today (as built) | Target (per architecture docs) |
|---|---|---|
| **Agent orchestration** | **LangGraph** state graph (`graph/learning_plan_graph.py`, `graph/content_graph.py`) — Learning-Plan + Content DAGs. CrewAI is a non-goal — parallel LangGraph nodes already cover the concurrency it would add | **LangGraph + CrewAI** — LangGraph state-graph/DAG control + CrewAI role-based specialist crews |
| **Grounding / web search** | Live DuckDuckGo (`ddgs`) in the Content dual-path Factual agent | RAG pipelines + agentic web search (Playwright/Firecrawl) |
| **LLM** | Groq key-**pool** primary (`openai/gpt-oss-120b`, rotated across `GROQ_API_KEYS`) → local **Ollama** fallback (`qwen2.5:7b`/`:14b`) (`config.py`, `llm_providers.py`). Keys are optional — with none set the app runs on Ollama alone | Multi-vendor **Model Router** (GPT, Claude, Gemini, Bedrock) |
| **Frontend** | **Next.js** (`web/`) | Next.js 14 / React 18 + JupyterLite |
| **Backend** | **FastAPI** (`api/`) | FastAPI microservices + Celery/Redis workers |
| **Memory / data** | **PostgreSQL** — learning sessions (JSONB) + per-user RAG vectors (pgvector); in-memory store for zero-infra dev (`MINDMORPH_STORE=memory`). No Redis/S3 | Pinecone (long-term) + Redis (short-term) + PostgreSQL + S3 |
| **Grounding / RAG** | **Opt-in local RAG** (FastEmbed + pgvector/in-memory, `rag/`, `MINDMORPH_RAG=1`) — augments web search, never replaces it | RAG pipelines + agentic web search (Playwright/Firecrawl) |
| **Observability** | LangSmith hooks (optional) + per-invocation token/cost accounting (`services/cost.py` `TokenMeter`) + per-stage wall-clock spans (`services/timing.py`, surfaced on `learning_session.timing` / `lessons[node].timing` and as `timing:` log lines); no metrics backend/exporter | Prometheus/Grafana/OpenTelemetry + Prompt Registry feedback loop |

The repo is a **working multi-agent prototype** with the **core P0-P1 learning loop implemented**: the Learning-Plan DAG
(orchestrate → scout → academic/market/practical → consensus → reviewer → Skill Dependency Graph),
the dual-path Content DAG (creative + live web grounding → synthesizer), the Exercise DAG + live
grading, and the FastAPI + Postgres backend all run end-to-end. CrewAI is a non-goal — parallel
LangGraph nodes already cover the concurrency it would add. Still open: multi-vendor LLM routing
beyond the current Groq-pool → local-Ollama chain, and the infra layer. Observability is partial —
token/cost accounting and wall-clock stage spans are recorded in-process and ride the session payload,
but nothing exports them to a metrics backend (see roadmap below).

---

## 2. Status by Agent (Learning-Plan DAG)

| Agent / Step (target) | Status | Code path | Notes |
|---|---|---|---|
| Orchestrator Agent (route SCOUT/CONTENT/EXERCISE) | ✅ | `agents/orchestrator/orchestrator_agent.py` | Structured output; routes inside the LangGraph graph (`graph/learning_plan_graph.py`). SCOUT, CONTENT and EXERCISE all wired to real nodes; only an unrecognized route hits the placeholder. |
| Scout Agent (decompose → Academic/Market/Practical queries) | ✅ | `agents/scout/scout_agent.py` | Returns `ScoutOutputSchema`; "Query" and "Prompt" variants. |
| Academic Agent (check university curricula) | ✅ | `agents/academic/academic_agent.py` | Real agent + `prompts/academic_prompt.py` (university-curriculum framing). Replaced the old inline `llm.invoke`. |
| Market Agent (scan job postings) | ✅ | `agents/market/market_agent.py` + `tools/job_scrapper_tool.py` | Apify LinkedIn MCP scrape + LLM summarize; runs as a graph node (degrades to None on empty scrape). Losing MARKET grounding now logs a WARNING at the consensus fan-in (`consensus_node`) — a degraded skill graph is no longer indistinguishable from a grounded one. The MCP handshake (~4.3s) is cached on the per-process `JobScraperService` and reset on any failure, so a wedged client can't persist. |
| Practical Agent (find GitHub projects) | ✅ | `agents/practical/practical_agent.py` | GitHub MCP wired in (`tools/github_mcp_client.py` `search_github_repositories` now returns results); folded into the practical prompt. Every way of returning None (no token / empty result / exception) logs its reason at WARNING. Scout prose is normalised to a bounded keyword query by `to_search_query` before it reaches GitHub's `q` (empirical 200-char cap, ≤6 terms — the documented limit is 256 but shorter queries were observed rejected). |
| Consensus Agent (combine findings → skill graph) | ✅ | `agents/consensus/consensus_agent.py` | Structured `SkillGraph` (nodes/edges); fan-in node after the three specialists. Runs `prune_dangling_edges` on the result **before** the Mermaid render and before the Reviewer, so the persisted graph, the diagram and the review all see one coherent artifact; dropped edges log at WARNING. |
| Reviewer Agent (quality/coherence) | ✅ | `agents/reviewer/reviewer_agent.py` | Structured `ReviewResult` (passed + notes). A failing or null verdict now logs loudly at WARNING (`reviewer_node`) instead of riding silently in the payload — but still **no retry loop and no consumer acts on the verdict**; the session is served either way. The one deterministically repairable failure (dangling edges) is fixed upstream in `consensus_node`; everything else needs a human. |
| Final Learning Plan (skill dependency graph) | ✅ | `graph/skill_graph_render.py` | Skill Dependency Graph artifact: JSON + deterministic Mermaid; rendered in the app. |

## 3. Status by Agent (Content-Generation DAG)

| Step (target) | Status | Code path | Notes |
|---|---|---|---|
| Content generator (Boost/Builder/Sprint formats) | ✅ | `agents/content_generator/content_agent.py` | All 3 formats (A/B/C); now wired via the CONTENT route into `graph/content_graph.py`. |
| Creative LLM (engaging draft) | ✅ | `content_agent.py` | Path A of the dual-path content graph. |
| Factual Agents (live web search) | ✅ | `agents/factual/factual_agent.py` | Path B — live DuckDuckGo search (`ddgs`), degrades to None on failure. |
| Synthesizer (merge creative + factual) | ✅ | `agents/synthesizer/synthesizer_agent.py` | "Master LLM" merges Path A + B; cites source URLs. Falls back to creative draft if no findings. |
| Visual Generator (diagrams) | ✅ | `agents/visual_generator/` | Mermaid concept diagram; rejects non-diagram output → None. Rendered in `web/MermaidDiagram`. |
| Example Generator (code examples) | ✅ | `agents/example_generator/` | One worked code example in the path's language (MERN→JS). Best-effort → None. |
| Assembler | ✅ | `graph/content_graph.py` `assembler_node` | Deterministic (no LLM): appends present `## Worked example` / `## Visual overview` sections after the synthesized body. |

## 4. Status by Agent (Exercise-Generation DAG)

| Step (target) | Status | Code path | Notes |
|---|---|---|---|
| Format Selector / GitHub / Blog / Dataset agents | ✅ | `agents/exercise/format_selector_agent.py`, `graph/exercise_graph.py` | LangGraph Exercise DAG: format selector → [GitHub MCP \| Blog (ddgs) \| Dataset (ddgs links)] fan-in. EXERCISE route now live (no more placeholder). |
| Synthesizer / Grading Setup (unit tests + rubric) | ✅ | `agents/exercise/exercise_synthesizer_agent.py`, `agents/exercise/grader_agent.py` | Synthesizer personalizes; Grader emits unit tests (coding) or rubric (case study). |
| **Live auto-grading** | ✅ | `tools/code_executor.py`, `grader_agent.grade_submission` | Python lightweight runner (sync/async, no pytest dependency in child), Node.js 20 native test runner (existing suites and exported test functions), SQLite schema/data exercises. SQL assertions share one validated JSON-array contract; verification queries are read-only. Empty, malformed, fixture-dependent or generator artifacts cannot earn credit. Separate result files, process exit checks and bounded diagnostics prevent stdout-based or unfinished-test grades. Harness defects leave mastery unchanged. Local hang-guard, not a security sandbox. |

## 5. Status by Architecture Layer

| Layer | Status | Notes |
|---|---|---|
| Frontend | ✅ | **Next.js** (`web/`) is the product; legacy Streamlit `app.py` **retired** (P3 #12). JupyterLite in-browser scratchpad embedded in the lesson. |
| Application Service | 🟡 | **FastAPI** (`api/`) exposes the loop over HTTP; agents + LangSmith prompt registry exist. Still **no** Celery, gateway, rate limiting. |
| AI / LLM | 🟡 | **LangGraph** orchestration ✅ (CrewAI is a non-goal — parallel LangGraph nodes cover it). Grounding: live web (DuckDuckGo) **+ opt-in local RAG** (FastEmbed + InMemoryVectorStore, `rag/`) in the Content dual-path. **Groq key-pool → local-Ollama provider chain** ✅ (`config.get_chat_model`, `llm_providers.ProviderChain`) — keys optional; zero-key clones run entirely on local Ollama. **Eval pipeline** 🟡 — offline content-groundedness LLM-judge (`evals/`, `python -m evals.run`). **Prompt Registry**: ✅ via `prompts/prompt_registry_wrapper_method.py` (LangSmith). |
| Data | 🟡 | **PostgreSQL** ✅ — learning sessions (JSONB, #6) + per-user RAG vectors (**pgvector**, #9). Still **no** Redis/S3/Kafka. |
| Infrastructure | 🟡 | Test CI exists: Python suite on Windows/Linux with Node.js, plus Playwright. No deployment pipeline, K8s/Terraform or Prometheus. |
| Analytics & Continuous Improvement | ⛔ | No telemetry pipeline, warehouse, or human-review loop. |
| LLM Ops & Production | 🟡 | Provider chain (Groq pool → Ollama) ✅ + content-groundedness eval ✅ (`evals/`) + **token/cost accounting** 🟡 (`services/cost.py` `TokenMeter`, priced per model, on the lesson-compose path only — see Cross-cutting) + **latency observability** 🟡 (`services/timing.py` wall-clock spans on the graph / lesson / grade paths — log lines plus a dict on the session payload; no metrics backend or exporter, see Cross-cutting). Still **no** deployment pipeline. |
| Security & Governance | ⛔ | No auth/RBAC/encryption/PII scrubbing. Only `.env` secrets + `.gitignore`. |

## 6. Supporting components

| Component | Status | Code path | Notes |
|---|---|---|---|
| Prompt Registry wrapper (LangSmith) | ✅ | `prompts/prompt_registry_wrapper_method.py` | `setup_agent_prompt()` reused by all agents; optional LangSmith push. |
| Job scraper tool (Apify MCP) | ✅ | `tools/job_scrapper_tool.py` | `JobScraperService` — LinkedIn search + parse. |
| GitHub MCP client | ✅ | `tools/github_mcp_client.py` | `search_github_repositories` returns results; wired into the Practical node (degrades to None without a token, and says which failure it was at WARNING). `to_search_query` folds typographic punctuation, strips stopwords and bounds the query (≤6 terms, ≤200 chars) so prose can't trip GitHub's `q` length limit. |
| Web search (DuckDuckGo) | ✅ | `agents/factual/factual_agent.py` | `ddgs` live search for the Content Factual path. |
| Skill graph renderer | ✅ | `graph/skill_graph_render.py` | Deterministic SkillGraph JSON → Mermaid. |
| LLM config | ✅ | `config.py` | Groq `openai/gpt-oss-120b` / local Ollama `qwen2.5:7b`\|`:14b`, temp 0.1. `GROQ_API_KEYS` (comma-separated pool) is optional and validated nowhere — absent/empty simply selects the Ollama path. |
| Next.js frontend | ✅ | `web/` | SCOUT skill-graph + CONTENT dual-path, Mermaid render, lesson view, grading. Legacy Streamlit `app.py` **retired** (P3 #12) — it no longer exists in the repo. |
| Tests | 🟡 | `tests/`, `web/tests/` | 16 October verification: pytest **385 passed, 3 DB-gated checks skipped on each of Windows and Ubuntu**, across 35 files; Playwright **44 passed** across 7 specs. Includes real Python/JS/SQL runner regressions and HTTP grading/mastery checks, plus existing graph/RAG/provider/assessment/tutor/observability tests. Type checking and clean production build pass. Real DB durability and live AI quality remain separate checks. |

---

## 7. Prioritized Roadmap

Each item notes the **architecture section** it satisfies and the **code gap** it closes.

### P0 — Orchestration foundation ✅ COMPLETE
1. ✅ **Migrated to LangGraph** (CrewAI is a non-goal — parallel LangGraph nodes already give the
   same concurrency). State graphs encode the Learning-Plan + Content DAGs (§6):
   `graph/learning_plan_graph.py`, `graph/content_graph.py`. *Satisfies:* §5.3 AI/LLM Layer.
2. ✅ **Academic & Practical are real agents**; GitHub MCP client wired into Practical
   (`agents/academic/`, `tools/github_mcp_client.py` now returns results).
3. ✅ **Consensus + Reviewer agents** complete the Learning-Plan DAG and emit a real **Skill
   Dependency Graph** (JSON + Mermaid). *Satisfies:* §3.1, §6.2. Reviewer has no retry loop yet.
4. ✅ **Content generator wired** via the CONTENT route; **dual-path content** built (Creative LLM +
   Factual DuckDuckGo agent + Master synthesizer). *Satisfies:* §3.2, §6.3.

> **Non-goal, not deferred:** CrewAI crews. The architecture doc calls for LangGraph **+** CrewAI,
> but parallel LangGraph nodes already cover the concurrency role-based crews would add — there is
> no plan to add CrewAI. The Visual/Example/Assembler tail of the
> Content DAG (§6.3) is now **built** (`MINDMORPH_RICH_CONTENT`, default on): synthesizer →
> [example ∥ visual] → deterministic assembler, with Mermaid rendered in the web lesson view.

### P1 — Close the core learning loop
5. ✅ **Exercise pipeline** — Format Selector + GitHub/Blog/Dataset agents + Synthesizer +
   **Grading Setup** (unit tests for code, LLM rubric for analysis) + **live auto-grading**
   (`tools/code_executor.py`). *Satisfies:* §3.3, §6.4. *Closed:* EXERCISE "Under development".
   The learn → plan → content → **practice + grade** loop now runs end-to-end on LangGraph.
   Deferred: containerized grading sandbox (Evaluation Service), real dataset ingestion.
5b. ✅ **Adaptive learning loop** — clickable skill graph → composed lesson (content + embedded
    exercise) → live grade → **mastery capture** (sticky per-node status overlay) → **adaptation**:
    a graded score mutates the graph (remedial prerequisite nodes on a low score / unlock edges on
    mastery) and triggers **score-aware regeneration** of the failed node's lesson against the gaps.
    `graph/lesson_graph.py`, `agents/adaptation/`, `graph/skill_graph_adapt.py` (deterministic,
    id-stable merge). State lives in the `learning_session` blob (originally Streamlit session state;
    persisted to Postgres JSONB by #6).
    Postgres persists `node_state`/`lessons` via #6; Redis remains deferred.
6. ✅ **Persistence + backend** - FastAPI (`api/`) exposes create/list/get/open/grade/assessment/chat.
   Postgres stores each learning session in JSONB, keyed by `(user_id, session_id)`; repository reads
   carry an out-of-band revision and updates compare/increment the row revision atomically. A stale
   save returns **409**, including when a tutor stream finishes after another mutation. Database
   failures return **503** and never use a hidden memory fallback. Explicit `MINDMORPH_STORE=memory`
   remains process-local development/test storage with the same conflict contract.
   **Schema:** run Alembic through `0003_session_revision`; startup no longer auto-creates tables.
   `/health` is liveness; `/ready` checks connectivity and migration head (503 if unavailable/pending;
   memory reports `durable: false`). Connection, query, lock and pool waits are bounded.
   **Verified 21 October:** actual JSONB reads, simultaneous updates, lessons/mastery/assessment reads
   through two fresh API processes, pgvector user isolation, actual database outage and recovery.
   Conflict handling deliberately rejects stale changes; it does not silently merge or repeat grading.
   *Deferred:* Redis and public identity/ownership (P3 #13). *Satisfies:* architecture 5.2, 5.4.
7. 🟡 **Provider chain (Groq pool → Ollama) + grounded learning** — _P1 #7 grounding implementation:_ a fourth
   Knowledge Grounding Agent retrieves personal/shared/public evidence without another rewriting
   LLM call. Consensus explicitly joins all four branches and Reviewer sees the evidence/profile.
   The common helper supplies scored/deduplicated passages, local MiniLM reranking, hard budgets,
   bounded public-page fetching, validated source references and explicit evidence status. New
   lessons/practice share one frozen bundle; tutor retrieval includes the selected skill and saves
   source metadata. Personal uploads and opt-in shared material are searched independently; unrelated
   uploads are excluded and saved paths/lessons remain intact. New text-PDF passages retain real
   pages/source identity; legacy provenance stays unknown. Grounded paths bypass shared roadmap
   caches/checkpoints. Session state enforces prerequisites; atomic Neo4j mirrors isolate each
   caller/session and reject stale revisions. Migration heads merge before nullable provenance fields.
   Verification includes disposable migration/preservation/restart/outage/isolation checks and
   100,000 passages with ten simultaneous exact tenant-filtered searches; that capacity fixture uses
   deterministic vectors with web/ranking disabled, separately from real local-model quality samples.
   See [GROUNDING.md](GROUNDING.md) for current contracts and verification commands. ANN indexing,
   OCR, library selection/removal and additional formats remain deferred. _Provider chain:_
   `llm_providers.py` + `config.get_chat_model(tier, provider, fallback)`: two vendor choices
   (`groq` | `ollama`). **Primary** = `provider` arg or `MINDMORPH_LLM_PROVIDER` env (defaults to
   `groq` when `GROQ_API_KEYS` holds keys, `ollama` otherwise); **fallback** = `fallback` arg or
   `MINDMORPH_LLM_FALLBACK` env (default `ollama` | `none` to disable). Groq keys are **optional** —
   `config.py` validates nothing, and asking for `groq` with an empty pool silently returns the local
   model, so a clone with no `.env` runs on Ollama alone. `ProviderChain` holds **one `ChatGroq` per
   key** (`max_retries=0`, so a 429 surfaces to our layer) and walks the pool in order: it **rotates to
   the next key** on `{401,403,408,413,429,500,502,503,529}`; treats anything else — **404
   model-not-found above all** — as permanent (logs ERROR, stops trying keys, drops to the fallback);
   and **re-samples once on the same key** for structured-output parse failures
   (`OutputParserException` / pydantic `ValidationError`), which are sampling variance, not a bad key.
   Exhausting the pool falls through to Ollama. Structured output is
   `with_structured_output(..., method="json_schema")`, mapped over every member (fallback included)
   across every structured-output agent (orchestrator, scout, consensus, reviewer, assessment, format
   selector, grader, adaptation); `astream` rotates only *before* the first token, so a
   half-delivered tutor reply is never restarted. Two tiers: `default` and `complex`
   (Consensus/Reviewer/Scout). Locally they differ (`qwen2.5:7b` vs `qwen2.5:14b`); on Groq
   `MINDMORPH_GROQ_MODEL_COMPLEX` **defaults to the same `openai/gpt-oss-120b`**, so the tier split
   only bites on Ollama unless you override it.
   Remote multi-vendor HTTP routing (Anthropic/OpenAI/Gemini) still open. *Satisfies (partial):* §5.3, §5.7.

### P2 — Personalization & ingestion
8. 🟡 **Onboarding + Dynamic Skill Assessment** - **21 October additions:** follow-up questions use
   validated IDs/options and normalized language/scope values; collected answers become a learner
   profile supplied to research, consensus, diagnostics, lessons, examples, exercises and tutor context.
   Non-coding domains skip job/GitHub/dataset coding sources and use written practice. Quiz retries
   are idempotent, correct answers preserve existing mastery/attempts, and saved review results use
   question IDs. Skip All continues; failed/empty follow-up responses offer explicit continuation.
   Newly generated/opened exercises carry revisions; stale/missing revision submissions and locked
   node submissions are rejected. Structural validation checks graph IDs, levels, edges and cycles;
   initial generation has one repair attempt; invalid adaptations preserve the grade and original graph.
   **Two live follow-up/roadmap samples passed; broader AI teaching quality remains unverified.** Existing diagnostic behavior follows: after a path
   is created, `SkillAssessmentAgent`
   (`agents/assessment/`, structured-output MCQ from the skill graph) generates a diagnostic quiz stored
   in `learning_session["assessment"]`. `POST /sessions/{user}/{session}/assessment` grades it:
   correct answers give those nodes a **head-start (`in_progress`, score `ASSESSMENT_PASS_SCORE`=60), NOT
   `mastered`**, without counting an exercise attempt — one MCQ isn't mastery, and full-mastering on a perfect
   quiz would empty the path (the learner still studies; passing the node's exercise upgrades it). Unknown/
   hallucinated `node_id`s skipped (no phantom node_state). Web quiz UI (`web/components/AssessmentQuiz.tsx`)
   gates the session page until submitted/skipped. Quiz generation is best-effort (failure → no quiz, path
   still loads). **Lightweight onboarding** — real **social sign-in deferred to P3 #13**; MVP login stays
   localStorage. *Satisfies:* §2.
   - **Path-language grounding:** lesson content is generated with the overall goal as `path_context`
     (`generate_content(..., context=ls["summary"])` threaded route→lesson→content graph), so a generic
     prerequisite like "Programming Fundamentals" is taught in the path's language (JavaScript for a MERN
     path, not Python). Verified live (MERN ctx → JS, Python ctx → Python).
   - **Exercise languages:** executable challenges support **Python, JavaScript (Node.js 20), and
     SQLite SQL**. Aliases (`py`, `js`, `node`, `sqlite`) normalize before routing. The cached exercise
     language must agree with its grading artifact; explicit unsupported execution languages never
     silently use Python. Other languages/conceptual exercises use `case_study`. SQL currently means
     learner-provided schema/data followed by verification SELECTs; query-only seeded-table exercises
     are not implemented. Learner-profile/content/example wiring is implemented and regression-tested; live language adherence and teaching quality remain unverified.
9. ✅ **User material ingestion** — `POST /users/{user_id}/knowledge` (multipart PDF) → **PyMuPDF**
   extract (`rag/pdf.py`) → chunk → **per-user** RAG store (`rag/registry.py`), keyed by user_id, with a
   **web upload UI** (`web/components/KnowledgeUpload.tsx`). The content graph resolves the retriever
   **per request** from `state['user_id']` (`graph/content_graph._resolve_retriever`), so a user's
   uploaded material grounds their lessons (merged with web search). `user_id` threads route →
   `open_lesson` → `_run_lesson` → lesson/content graphs. **Persistence:** durable **pgvector** store
   (`rag/pg_store.PgRagStore`, `rag_chunks` table, migration `0002`) selected by `MINDMORPH_STORE=postgres`
   (same switch as the session repo); in-memory store backs zero-infra dev/tests. Verified live (real PDF
   → FastEmbed → pgvector ingest + cosine retrieve, per-user isolation). *Deferred:* per-session scoping,
   OCR for image-only PDFs, ANN index (ivfflat/hnsw) at scale. *Satisfies:* §2, §5.4.

### P3 — Full product surface
10. 🟡 **AI Teaching Assistant** — streaming chat tutor (`agents/tutor/`) grounded in the open lesson +
    the learner's RAG material (`build_tutor_messages` pulls `lessons[node_id]` + per-user
    `RagStore.retrieve`). `POST /sessions/{user}/{session}/chat` streams tokens over **SSE** and persists
    each turn to `learning_session["chat"]` (JSONB; user message saved before streaming so it survives a
    dropped stream). ✅ **Streams through the Model Router** — `TutorAgent` now calls
    `get_chat_model(tier, provider, fallback, temperature=…)` with tutor-specific overrides readable from
    `MINDMORPH_TUTOR_PROVIDER` / `MINDMORPH_TUTOR_FALLBACK` / `MINDMORPH_TUTOR_TIER` env vars;
    `ProviderChain.astream` rotates keys only before the first token, then falls back to Ollama. Web
    `TutorChat.tsx` (buffered SSE-over-fetch) on the session page. Verified live (real Groq → 74 token
    frames). *Deferred:* **voice** (Whisper STT / ElevenLabs TTS), multi-thread chats. *Satisfies:* §2.
11. **Screen Vision + Browser Automation agents.** *Satisfies:* §2.
12. 🟡 **Next.js 15 / React 19 frontend** (`web/`) — brought forward alongside #6 (it's what justifies
    the HTTP backend). Dark-luxury UI consuming the FastAPI API via TanStack Query: localStorage login,
    session list/resume, **react-flow skill graph with real clickable nodes** (the upgrade the Streamlit
    iframe couldn't do), status-colored + prereq-locked nodes, lesson view (react-markdown), **Monaco**
    code editor, live grade → mastery re-color + adaptation. Build + typecheck green; Playwright E2E
    (mocked API) covers the full loop + lock gate. **Verified live against the real FastAPI + Postgres:**
    login → the persisted adapted session loads through all layers and the 8-node graph renders (CORS
    preflight + read path proven; browser-driven grade is the one chain not re-run, its parts covered by
    curl write + mocked-client E2E). **P3 #12 complete:** legacy Streamlit `app.py` **retired** (learning
    logic already in `services/`; the two tests that imported `app` repointed there) and a **JupyterLite**
    in-browser Python scratchpad (`web/components/Sandbox.tsx`, hosted-REPL iframe, `NEXT_PUBLIC_JUPYTERLITE_URL`)
    embedded in coding lessons. *Satisfies:* §5.1.
13. **Infra, analytics, security layers** — K8s/Terraform, Kafka/Airflow/BigQuery, Auth0/RBAC/encryption.
    *Satisfies:* §5.5, §5.6, §5.8.

### Cross-cutting
- 🟡 **Test suite** — pytest (`tests/`, 385 passed, 3 DB-gated skips on Windows and Ubuntu, verified 16 October 2026: graph routing/fan-in, content dual-path, skill-graph
  render + dangling-edge prune, RAG/ingestion/pgvector, assessment, tutor chat, provider-chain rotation/fallback,
  cost/usage accounting, stage timing, GitHub query normalisation, funnel events, import guards) plus Playwright
  (`web/tests/`, 44 passed: mocked-API E2E loop + lock gate, onboarding/summary navigation, actual Monaco
  JS/SQL language checks, harness-error feedback, dagre framing, Mermaid containment and lock labels).
  Real runner regressions additionally cover async failures, empty tests, legacy artifacts, SQL
  assertion validation, language disagreement, spoofed stdout, bounded errors and descendant cleanup.
- 🟡 **Funnel instrumentation (Gate-1)** — `services/events.py` appends a structured event to an
  append-only `learning_session["events"]` timeline at every loop chokepoint (`session_created`,
  `assessment_submitted`, `lesson_opened`, `exercise_graded`, `node_mastered`/`needs_review`,
  `lesson_regenerated`, `path_completed`, `compose_failure`/`grade_failure`, `content_flagged`).
  `funnel_summary()` derives drop-off + content-quality aggregates on read (single source of truth).
  Content quality measured two ways: **proxy** (low scores + regenerations) and an **explicit** learner
  thumbs-down (`POST …/lessons/{node}/flag` → `content_flagged`, surfaced in `web/LessonPanel`).
  Operator view `GET /admin/funnel` (shared-secret `x-admin-token` gate, fails closed) aggregates
  across all sessions via `repo.list_all()`. Persists in the JSONB blob like `usage`. *Deferred:*
  dashboards/Kafka (P3 #13), broader per-user analytics. **21 October: per-skill practice history**
  now shows valid recorded scores and local dates below the lesson/result, newest five first with
  Show all/Show less. It updates after grading and survives reloads or a cleared lesson cache;
  diagnostic quizzes and grading-system failures are excluded. No new API, database migration or AI
  calls. **Verified: 415 backend tests (real database/outage checks, zero skips), 55 browser tests,
  type checking and a clean isolated production build passed.** Cross-session per-user analytics
  remains open. De-risks the Gate-1 "watch real users" checkpoint.
- 🟡 **Cost observability (unit economics)** — `services/cost.py` `TokenMeter` (a LangChain callback
  attached to the lesson-graph invocation in `_run_lesson`) aggregates token usage across all nested
  LLM calls; `estimate_cost` prices it off `MODEL_PRICES` (Groq `openai/gpt-oss-120b` at $0.15/$0.60 per 1M
  in/out; local `qwen2.5:*` at a **real** $0.00 — self-hosted, not a placeholder). An unpriced model
  id or a call reporting no usage sets `unknown`, which stays distinct from a priced $0. `open_lesson` records per-lesson usage on the cached entry and
  accumulates `learning_session["usage"]` (`composes`, `cache_hits`, `tokens_in/out`, `est_cost_usd`),
  logging each open HIT/MISS. Rides in the JSONB blob (persists across restart) + the existing
  `SessionResponse`. Live: one compose ≈ 5.2k in / 2.5k out tokens ≈ **$0.002** on Groq at the prices above
  (an earlier note said $0.005; that predates the current `MODEL_PRICES` and doesn't reconcile). Pairs with the
  already-durable per-`node_id` lesson cache — together the two halves of the "$/active-user" hard
  gate. *Deferred:* DAG/assessment/tutor cost, per-user aggregation, dashboards.
- 🟡 **Latency observability (where the time goes)** — `services/timing.py`: one `span()` records a
  wall-clock stage into a `ContextVar` collector and always emits a `timing: <stage> <ms> ms` log line, so
  it works with no collector attached. Instrumented at the LLM-backed chokepoints — `graph.orchestrator`
  / `graph.scout` / `graph.academic` / `graph.market` / `graph.practical` / `graph.consensus` /
  `graph.reviewer` (plus the four `market.apify.*` / `market.llm.*` sub-spans),
  `lesson.content` / `lesson.exercise`, and the grade exec + `grade.adapt`. Each span carries a
  `start_ms` offset from the collector origin, so the specialist fan-out can be *seen* to overlap rather
  than inferred from durations. Surfaced on `learning_session["timing"]` (`graph`, `grade`) and per-lesson
  `lessons[node_id]["timing"]`, riding the JSONB blob beside `usage`. Replaces the throwaway monkeypatch
  harness every previous performance question needed. *Deferred:* no exporter, no metrics backend, no
  dashboards; DAG/assessment/tutor token-level latency is not broken out.
- ✅ **Graph integrity — one invariant, enforced in three lanes.** An edge naming a node id that does not
  exist (an LLM hallucination — the observed case was Consensus emitting `mlops_tools`) becomes an
  unmasterable prerequisite: its dependants lock forever and `is_session_complete` can never fire.
  `graph/skill_graph_adapt.prune_dangling_edges` is the single implementation, applied at **build time**
  in `consensus_node` (before the Mermaid render and before the Reviewer), at **read time** in
  `services/completion.prereqs_by_node` (the sole leak point every consumer routes through), and mirrored
  in TypeScript by `web/lib/status.ts` `prereqsByNode` so the raw slug can't reach the lock message or a
  card's accessible name. The read-time guard is what rescues sessions **already persisted** with a
  phantom: nothing stored is rewritten and the stored edge list stays byte-identical, so there is no
  migration — such a session simply becomes finishable on next read. Read-time drops log at WARNING,
  deduped per shape (the derivation runs several times per request). `node_state` keys are deliberately
  *not* filtered — `apply_adaptation` is additive-only, so a stale id there is an impossible state.
- 🟡 **Eval pipeline** — `evals/`: offline **content-groundedness LLM-judge** (`python -m evals.run`,
  `--calibrate` validates the judge discriminates contradiction vs extra-but-correct). Judges precision
  (contradiction of curated source facts), not coverage. De-risks the Gate-2 "content trust" hard gate.
  Deferred: grading-correctness harness (covered by `test_code_executor`), score-history tracking, CI wiring.
- ✅ Fixed the package-name typo `agents/orchertrator/` → `agents/orchestrator/`.
- ✅ **MCP calls are timeout-bounded** — every MCP network await (`get_tools`/`ainvoke`/`search_jobs`/
  `fetch_job_results`) is wrapped in `tools/mcp_timeout.with_mcp_timeout` (`asyncio.wait_for`, default
  20s via `MINDMORPH_MCP_TIMEOUT`). A flapping/half-open SSE connection now degrades the market/practical
  fan-out node to `None`/`[]` instead of wedging the whole request at the consensus fan-in; the reused
  `JobScraperService` client is reset on timeout so the next request rebuilds clean. Removes one
  indefinite-hang failure mode (the per-call timeout is the "gather guard" — parallelism is LangGraph's
  fan-out, not an explicit `asyncio.gather`).
