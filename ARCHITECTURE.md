# ARCHITECTURE — AllMai AI Platform (extensible, multi-assistant)

> Phase 0 status: repo structure, FastAPI skeleton, PostgreSQL+pgvector schema
> with forced RLS, Redis/Celery/MinIO wiring, Next.js + Flutter skeletons,
> ENV configuration, migration `0001_initial`, 17 passing tests.
> No containers: every service runs as a direct OS install.
> Client strategy (updated): **Next.js web app only** — one flat web
> application serves teacher flows (lesson plans) and student flows
> (coach, study plan, progress, chat). The Flutter skeleton stays in
> `mobile/` but is DEFERRED until after the web app ships.

## 1. Mental model

```text
User
 ↓
Workspace            ← the tenant boundary (personal | shared)
 ├── Knowledge Bases → Source → Document → Page/Segment → Chunk
 │                     → Concept / Entity / Relation → Embedding
 ├── Notes / Files   (Sources of type note/url, blobs in object storage)
 ├── Memories        (user facts, goals, plans, summaries — NOT knowledge)
 ├── Assistants / Agents (generic engine + registry, e.g. teacher/student)
 └── Conversations   (stateful, per agent + user)
```

## 2. Four separated engines

| Engine | Owns | Vendor position |
|---|---|---|
| Knowledge Engine | `KnowledgeBase→Source→Document→PageSegment→Chunk→Concept/Relation→Embedding`; retrieval = vector + metadata filter + FTS + RRF (+ reranker later) | RAG-Anything ONLY behind `ParserPort` (`app/knowledge/parsers/`). Source of Truth = our PostgreSQL + object storage |
| Memory Engine | user-related data: preferences, goals, facts, plans, decisions, summaries | `MemoryProvider` protocol; MVP = `PostgresMemoryProvider`. Zep is NOT used in MVP; a future adapter implements the same protocol |

Memory (DONE): `remember` upserts by (workspace, user, key); `recall` is
token-overlap scoped to (workspace, user) with category boosts
(goal/plan/preference); `summarize` upserts per scope. Controlled
extraction policy: every agent chat turn writes a rolling per-agent
summary bound to the correct (workspace, user); durable facts are written
ONLY via explicit profile/plan endpoints — no LLM auto-facts (no junk,
no misattribution). Endpoints are
workspace-nested (`/workspaces/{id}/memory/facts|search|summaries`);
reading another user's memory needs owner/admin workspace role.
A vector upgrade can replace the scorer without changing callers.
| Agent Engine | generic `Agent{goal,instructions,knowledge,memory,workflow,state,tools,model,safety}`; `Assistant` = simple, `Agent` = stateful w/ tools | LangGraph is an internal runtime (`app/agents/runtime/` only). Clients use `AgentService` |

Agent engine (DONE): `AgentService` owns CRUD + chat + conversation/state
persistence; LangGraph `StateGraph` runs
load_memory → retrieve_knowledge → build_prompt → llm_call internally.
Streaming: `POST …/chat/stream` (generic) and `POST …/coach/chat/stream`
(student) emit SSE frames `meta → token* → done|error → [DONE]` reusing
the same graph nodes (retrieval/memory/prompt identical to sync chat);
persistence happens once after the stream. Teacher/student web pages
render tokens live with sending/streaming/error/done states + retry.
Per-agent tool allow-lists from the registry (teacher = knowledge only,
student = knowledge + memory). Safety = length caps, context/instruction
separation + preamble, linear graph (step cap by design). Model per agent
from `ai_settings` (`agent.<key>` → `chat.default` fallback).
Endpoints: `/workspaces/{id}/agents…`, `…/chat`, `…/conversations`,
`…/conversations/{cid}/messages`.

Teacher agent (DONE, `teacher_lesson_planner` in `app/agents/teacher/`):
input kb + chapter/grade/duration/style/teacher-instructions →
`hybrid_search` → Persian prompt → `generate_structured` (JSON-mode,
Pydantic-validated) → references attached from REAL chunks in code
(page/timestamp preserved, never LLM-hallucinated) → saved BOTH as a
`lesson_plans` structured row (migration 0003, tenant RLS — the queryable
Source of Truth for list/detail views) AND as conversation messages for
chat rendering. Endpoints: POST `…/lesson-plans`, GET `…/lesson-plans`
(list), GET `…/lesson-plans/{id}`, GET `/workspaces/{id}/lesson-plans/
by-conversation/{cid}` (detail view key).
Wrong agent key → 422; cross-workspace/user → 404.

Student agent (DONE, `student_academic_coach` in `app/agents/student/`):
profile (`profile.*` memory facts via `…/profile`), goals (durable `goal.*`
facts on plan creation), structured study plans (weeks/tasks with
service-stamped ids, `…/study-plans`) persisted in conversation state,
stateful `…/coach/chat` (profile + plan + progress + history + optional KB
in every prompt, rolling summary per turn), and `…/progress` with task-id
validation against the stored plan. Web plan/progress pages read
conversations + state + messages.

## 12. E2E proof

`backend/tests/test_e2e.py` runs the full chain in one test with mocked
AI + fake storage: register → workspace → KB → note source → real ingest
→ search → teacher agent (plan + chat with grounded citations) →
student agent (profile → plan → chat → progress) → outsider gets 404 on
knowledge/search/chat/memory. Backend suite green (`pytest`;
live-AI + live-RLS skip without services/flag);
`npm run build` green on 22 web routes.

Live AI: `tests/test_live_ai.py` runs REAL provider calls (chat, embeddings
+ dim parity, structured plan, /models reachability) only with
`RUN_LIVE_AI=1` and a configured key — the default suite stays offline.
| AI Provider | `generate / generate_structured / stream / embed` (+ retry on 429/5xx); keys server-side; models from ENV+DB | `openai_compat` (OpenAI **and** GapGPT via `OPENAI_COMPAT_BASE_URL`) + `gemini` (REST, incl. structured JSON + embeddings). `app/ai/factory.py` resolves the provider per purpose from ai_settings (`chat.default`/`embedding.default` provider fields) with ENV fallback — swapping vendors is config, not code |

Conformance is enforced by `backend/tests/test_architecture.py`: vendor SDK
imports outside their adapter directories fail the suite.

Model settings live in the DB (`ai_settings`, managed via
`GET/PUT /admin/ai-settings`, admin-only) and override ENV:
resolution order = DB row → ENV/code default. Keys: `chat.default`,
`embedding.default`, `audio.transcription`,
`agent.teacher_lesson_planner`, `agent.student_academic_coach`.
Cheap defaults (verified live against the GapGPT model list):
chat `gpt-4o-mini`, embedding `text-embedding-3-small` (1536d),
audio `whisper-1` + local fallback, agents `gemini-2.5-flash` (chat +
structured + tool rounds, all GapGPT-routed). The admin panel (Phase 11) edits
these; unknown keys are rejected (422). Per-agent provider/model/
temperature/max_tokens resolve as chat.default ← agent.\<key\> ←
definition.model_defaults (see `resolve_agent_chat`); prompt templates
(lesson_plan/coach_chat/study_plan) live on the definition with built-in
fallbacks. The student coach acts on plans via tools (get/create/progress)
inside the same chat turn.

## 3. Workspace isolation (the most important rule)

No query, retrieval, file access, memory access or agent access runs without
a workspace context.

- **Application layer**: `app/core/workspace.py::require_workspace_id` /
  `scoped_filter` — every tenant-aware service takes an explicit
  `workspace_id`; missing context raises `MissingWorkspaceContextError`.
  `resolve_workspace` (workspaces/service.py) is the single dependency every
  `{workspace_id}` endpoint must use: it provisionally binds the claimed
  workspace, then membership-gates (404 for outsiders, no existence leak).
  Membership (`workspace_members`) is checked per request.
- **Database layer**: migrations `0001`→`0005` enable **FORCED** Row Level
  Security on all 15 tenant tables. Final shape (`0005`, after live-run
  findings): three session GUCs — `app.workspace_id` (claimed workspace),
  `app.user_id` (JWT identity), `app.is_admin`. Members see own rows +
  own workspaces (via subselect); admins bypass; everything else needs the
  claimed context. Writes use `WITH CHECK` (0004 fixed USING-only policies
  that denied all INSERTs). `set_workspace_context()` binds the session and
  a SQLAlchemy `after_begin` listener re-applies `SET LOCAL` on every new
  transaction, so mid-request commits can't drop the binding; `get_db` and
  the worker reset all three GUCs on release. Celery tasks carry
  `workspace_id` in the message and bind before the first row access.
- **Tests**: `test_workspace_isolation.py` covers the app-layer filter on two
  workspaces with identical data + static verification of every RLS policy.
  `test_rls_live.py` proves the database itself blocks leaks (two tenants +
  empty context) against a live PostgreSQL and cleans up; it skips when no
  DB is reachable. `resolve_workspace` (workspaces/service.py) is the single
  dependency every `{workspace_id}` endpoint must use: fetch + membership
  gate (404 for outsiders, no existence leak) + `SET LOCAL` bind.

## 4. Knowledge design decisions

- One workspace → many knowledge bases → many sources (multi-source from day one).
- MVP source types: `pdf, docx, pptx, txt, md, image, audio, url, note`.
  `audio` = mp3/wav/m4a with transcription + `start_ms/end_ms` timestamps,
  semantic chunking, transcript persisted, time-aware retrieval.
  `video` is reserved and **rejected** by `validate_source_type` (Phase 0).
- Page numbers (documents) and timestamps (audio) are preserved on segments
  and propagated to chunks/citations.
- Raw files → MinIO/S3 (`storage_key`); PostgreSQL → metadata + structure.
- Embeddings: provider-swappable; model + dim from ENV
  (`EMBEDDING_MODEL`, `EMBEDDING_DIM=1536`); `chunks.embedding` is
  `VECTOR(1536)` with an HNSW cosine index. Changing the model later needs a
  re-index migration (architecture is ready, data migration is Phase N).
- Retrieval (Phase 6): pgvector cosine + `kb_id/workspace_id` filter +
  FTS (`content_tsv`, GIN) → RRF fusion → optional reranker seam
  (`RetrievalPort` in `app/knowledge/retrieval/base.py`).

## 5. Processing pipeline (DONE worker core)

```text
Upload → Storage → Source(row, pending) → Celery ingest_source task
→ parse → structure → chunk → embed → PostgreSQL → ready/failed
```

`run_ingest` (workers/tasks.py, injectable deps): download → `parse_source`
→ Document + PageSegments → word-chunks (400w/60 overlap, page + timestamp
preserving; audio merged in ~45s windows) → GapGPT embeddings (batched, 32)
→ `ready`; any exception → `failed` with message, never a crash.
Parsers: local fallback first (pypdf/docx/pptx/bs4-less text/url-note/image-meta);
RAG-Anything only when `USE_RAGANYTHING=true` (else deterministic fallback).
Audio (MVP): Transcription API only (`/audio/transcriptions`,
`verbose_json` timestamps → PageSegments → time-window chunks → retrievable;
no local models, no faster-whisper). Provider-agnostic: any
OpenAI-compatible transcription endpoint works via ENV. Images: pillow dims
+ gpt-4o-mini vision description, metadata-only on failure. URL sources
are fetched live at ingest (15s timeout, 2MB cap, HTML→text) with
descriptor fallback on failure. Concepts/relations enrichment is open.

## 6. Retrieval (DONE)

`hybrid_search` (retrieval/hybrid.py): pgvector cosine + `kb_id/workspace_id`
filter + FTS (`to_tsvector('simple')` — Persian-safe, no English stemming)
→ RRF(k=60) fusion → rerank (`retrieval.rerank` in ai_settings; heuristic
term-coverage reranker today, cross-encoder contract tomorrow; disable
with `{"enabled": false}`).
`content_tsv` is backfilled at ingest time on Postgres. Endpoints:
`POST /workspaces/{id}/knowledge/search` (any member) and
`POST /workspaces/{id}/knowledge/debug` (owner/admin only) returning the
full trace `query → chunks+scores+ranks → context → answer` with the
effective chat model from `ai_settings` plus `retrieval_ms` /
`generation_ms` timings. Unit tests run the same fusion
code on SQLite (pure-Python cosine + LIKE fallback).

Intake (DONE): nested routes `/workspaces/{id}/knowledge-bases…/sources`
(multipart file or `/link` for url/note). Validation =
extension allow-list + magic-byte sniffing + 50 MB cap (notes 5 MB);
`video` rejected. Raw bytes — including url/note descriptors — always go
to our StorageProvider (`workspaces/{ws}/kb/{kb}/sources/{id}/{file}`);
MVP backend is the server's local disk (`STORAGE_BACKEND=local`), S3
implements the same protocol for later. PostgreSQL keeps metadata only.
Files are never public: downloads stream through the authenticated
`…/sources/{id}/download` endpoint (membership-checked); presigned URLs
exist only for the s3 backend. `enqueue_ingest` pre-checks the broker
over TCP (1s) so uploads never hang when Redis is down — sources stay
`pending` for retry.
Downloads via short-lived presigned URLs (15 min), membership-checked,
storage keys namespaced per workspace (no cross-tenant key guessing).

## 6. Agents (generic engine, first two products)

`app/agents/registry.py::AGENT_CATALOG` — adding career/fitness/personal/
business/document assistants later = new registry entry + prompts/tools,
no engine rewrite.

- `teacher_lesson_planner` (agent): lesson plan from KB + chapter/grade/
  duration/style/teacher instructions → structured output (title, objectives,
  prerequisites, topics, activities, examples, questions, assessment,
  source references with chunk+page).
- `student_academic_coach` (agent): study planning/guidance/tracking/re-plans
  from profile + memory + goals (+ optional KB); stateful via
  `Conversation.state` + graph checkpointer.

No fine-tuning/training in scope.

## 7. API surface (ours — not Dify, not any vendor)

Auth, workspaces, knowledge-bases, sources/upload, knowledge/search,
memory/search, agents CRUD, agents/{id}/chat (SSE), conversations,
retrieval-debugger (admin), processing status. Routers with these prefixes
exist in Phase 0; handlers land per phase.

## 8. Security (Phase 1 done unless noted)

Auth: email+password (bcrypt, 8–72 chars), JWT access (15m) + opaque refresh
tokens (SHA-256 hashes in `refresh_tokens`, rotation on every refresh, 30d
expiry). `POST /auth/register|login|refresh|logout`, `GET /auth/me`.
RBAC: `is_admin` flag + `require_admin` dep; workspace roles (owner/admin/
member) land in Phase 2. Login failures + logins + refreshes are audit-logged
(`audit_logs`, audit meta PII-redacted). Rate limits (in-process sliding
window; Redis swap documented): login 10/min per IP+email, refresh 30/min,
chat 60/min per user, plans 20/min, debug 30/min. Prompt-injection:
length caps + context/instruction separation preamble; attempts are
audit-logged (`agents.injection_attempt`), never silently trusted. Intentionally global tables (no RLS, admin/app-gated):
`users`, `refresh_tokens`, `audit_logs`, `ai_settings`. Still to come:
rate limiting (login throttle), file validation hardening, prompt-injection
stripping, private buckets + signed URLs (Phase 3+).

## 9. Runtime topology (direct installs, no containers)

```text
PostgreSQL 16 + pgvector :5432 ─┐
Redis :6379 ────────────────────├── FastAPI (uvicorn :8000, .venv)
Local disk storage ─────────────├── Celery worker (same .venv, separate process)
                                 Next.js web app :3000 (teacher + student flows)
                                 Flutter: DEFERRED (skeleton only)
```
(No MinIO: upstream archived open-source builds; MVP stores files on
server disk behind the StorageProvider abstraction.)

## 10. Repo layout

```text
allmai/
├── ARCHITECTURE.md  README.md  .env.example  .gitignore  .venv/
├── backend/  app/{core,common,auth,users,workspaces,knowledge,memory,
│             agents,conversations,ai,storage}  workers/  alembic/  tests/
├── web/      (Next.js + TS + Tailwind, RTL — teacher + student + admin)
├── mobile/   (Flutter skeleton, DEFERRED)
└── infra/    (postgres_init.sql, service setup notes — no compose files)
```

## Web app routes (Next.js, replaces mobile for now)

```text
/teacher              home + assistants
/teacher/lesson-plans new plan (KB, chapter, grade, duration, style)
/teacher/lesson-plans/[id]  plan detail (from conversation messages)
/teacher/chat         free AI interaction
/student              home + coach
/student/plan         study plan (from coach conversation state)
/student/progress     progress view
/student/chat         AI chat
/admin/*              users, workspaces, KBs, sources, agents,
                      conversations, processing, ai-settings, debugger
```

## 11. Phase order

0. Setup (DONE) → 1. Auth (DONE) → 2. Workspace + membership + live RLS test (DONE) → 3. Storage + KB/Source APIs (DONE) → 4. Ingestion pipeline (DONE) → 5. Retrieval + debugger API (DONE) → 6. Memory engine (DONE) → 7. Agent engine + LangGraph (DONE) → 8. Teacher agent (DONE) → 9. Student agent (DONE) → 10. Admin web (DONE) → 11. Web app (DONE) → 12. Audit + production readiness (DONE) → next: new agents & hardening (rate limits, reranker)
(teacher + student flows; Flutter deferred) → 12. Hardening.
Each phase ends with `pytest` green; E2E: workspace→KB→sources→search→
agent→chat with zero cross-workspace leakage.
