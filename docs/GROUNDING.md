# Grounded learning — P1 #7

The Knowledge Grounding Agent is a fourth research branch alongside Academic, Market and Practical. It retrieves evidence without an additional rewriting LLM call. Consensus waits for all four branches, builds a structurally validated skill graph, and supplies evidence/profile to Reviewer. Review warnings remain visible.

The skill graph describes topics and prerequisite order; it is not a lesson/exercise catalogue. First opening a skill generates a lesson and practice in that learner's session. Later uploads do not rebuild saved paths, grant mastery, replace saved practice or change exercise revisions. New paths and future unopened/remedial lessons can use new evidence. Without uploads, normal paths and teaching remain available using relevant enabled shared/public material.

## Shared evidence

`rag/grounding.py` searches personal and enabled shared stores independently, combines public evidence, filters weak matches, deduplicates and reranks locally. Uploads do not suppress useful shared material. Source text is untrusted evidence and cannot override the goal, language, scope or required prerequisites. Internal source-ID filters leave a seam for P2 #9; an explicit empty filter never broadens retrieval.

Planning discovers candidates with Scout's concise subject phrase and ranks them against the actual learning goal. This avoids treating research instructions as the passage relevance question. A newly generated lesson passes its frozen evidence bundle and taught context to practice; the exercise skips independent GitHub/blog/dataset research. Tutor retrieval includes the selected skill and question, while public research uses only the original declared goal. Private lesson text, quoted questions and filenames are not web queries.

| Work | Bound |
|---|---|
| Ranking | 20 total candidates across sources; at most six passages |
| Evidence | At most 8,000 formatted characters; practice reserves room for up to 2,000 characters of taught context |
| Public research | Five results, three fetched pages, 20-second returned research deadline |
| Public page | HTTP(S), standard ports, three redirects, 2 MiB identity body |
| Local work | 12 admitted workers; eight-second store lookup and five-second ranking waits |
| Web admission | Two outstanding research jobs; busy/unavailable degrades explicitly |

Timeouts do not terminate arbitrary native library work: admission slots stay occupied until jobs actually finish, preventing unlimited accumulation. Blocking work stays outside async request loops. Embedding/ranking models are reused per process; warm them before measuring steady-state performance.

Fetches validate every destination/redirect, reject private/reserved addresses and credentials, and connect to the validated IP while checking HTTPS against the original hostname. They send no learner cookies. Search snippets and fetched pages are distinct source kinds.

New native-text PDF passages keep an opaque upload ID and real page number, with hard paragraph chunk limits. Legacy pages/groupings remain unknown. Scanned pages need OCR, which remains #9 work. Generated citations must reference supplied IDs/URLs; unsupported references are removed with an insufficient-evidence warning, while literal code is preserved. Paths, lessons, practice and saved/streamed tutor turns carry source/status metadata. Historical content without provenance remains readable and explicitly historical.

## Sessions, graph mirror and privacy

New grounded paths bypass shared roadmap cache reads/writes. Saved-session/lesson reuse remains. Caller context reaches sync/stream creation, standalone routes, lessons and tutor retrieval; it never comes from Scout-generated identity text.

Saved session graph and `node_state` always enforce locks, grades and completion. Neo4j mirrors successful session saves under a caller/session-derived graph key, with `(graph_key, skill_id)` skill identity and scoped progress/edges. Snapshot writes are atomic, publish completeness last and reject delayed older revisions. Legacy shared graph data stays intact. See [NEO4J_ARCHITECTURE.md](NEO4J_ARCHITECTURE.md).

Grounded execution checkpoints are disabled. Generation scopes disable automatic LangSmith traces and redact logs, including network query logs from library-owned threads. Diagnostics retain safe classes/provider HTTP codes only. External telemetry is allowlisted aggregate metadata; the owner's saved event timeline retains details. Relevant passages still go to the configured generation provider: local embeddings do not imply local generation. The MVP identity remains supplied user context, not authenticated sign-in.

## Configuration and upgrades

| Variable | Default | Meaning |
|---|---|---|
| `MINDMORPH_RAG` | `0` | Enables shared seed material; personal uploads remain independently eligible |
| `MINDMORPH_KNOWLEDGE_DIR` | `knowledge_base` | Lazily loaded shared `.md`/`.txt` corpus |
| `MINDMORPH_WEB_RESEARCH` | `1` | Enables common public research |
| `MINDMORPH_RERANK` | `1` | Local MiniLM reranker; failure uses bounded similarity fallback |
| `MINDMORPH_MODEL_CACHE` | Embedding library default; `logs/model-cache` for reranker | Optional common local model cache |
| `MINDMORPH_NEO4J_MIRROR` | `1` | Session mirrors; disabling does not disable prerequisite enforcement |

Embeddings remain 384-dimensional `BAAI/bge-small-en-v1.5`; ranking uses `Xenova/ms-marco-MiniLM-L-6-v2`. Groq and the existing Ollama fallback are unchanged. No new hosted vendor is added.

`0004_merge_heads` joins the cache/revision branches; `0005_grounding_provenance` adds nullable `rag_chunks.source_id` and `page`. Existing uploads, vectors and sessions remain intact. Back up non-disposable data, then explicitly run `conda run -n mindmorph alembic upgrade head`. Readiness checks the single head and new columns. Verification does not automatically migrate the live database, change storage mode or restart the memory-only application backend.

## Verification

```powershell
conda run -n mindmorph python -m pytest -q
conda run -n mindmorph python scripts/verify_grounding_storage.py
conda run -n mindmorph python scripts/evaluate_grounding.py
# Real configured generation, with synthetic material and market scraping disabled:
conda run -n mindmorph python scripts/smoke_grounding.py
# From web/:
conda run -n mindmorph npm run typecheck
conda run -n mindmorph npm run test:e2e -- --timeout=60000 --workers=1
```

The storage runner uses uniquely named disposable PostgreSQL/Neo4j containers. It tests each old head, both heads and fresh migration; legacy preservation; provenance after a fresh process; outages/recovery; graph isolation/revision ordering/rollback; and 100,000 passages with ten simultaneous exact tenant-filtered searches. Capacity fixtures use deterministic 384-dimensional vectors with web/ranking disabled, so they measure storage/helper capacity rather than semantic model quality. ANN indexing stays deferred.

Local quality uses real models and labelled coding/non-coding/no-match controls, including a verbose planning goal. Live fixtures use their own memory store, with application graph/telemetry writes and paid market scraping disabled. Reports are under ignored `logs/p1-7-implementation/`. Required DB tests fail instead of skip when `MINDMORPH_REQUIRE_DB_TESTS=1`; destructive legacy graph tests require an explicitly disposable target.

OCR, expanded formats, library selection/removal, in-place roadmap refresh, authentication, execution isolation, legacy cache SQL repair and Laya package compatibility remain separate work.
