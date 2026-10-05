# ARCHITECTURE — AllMai AI Platform (extensible, multi-assistant)

> Phase 0-12 (web) DONE + Phase 1 (Agent Definitions + Global Knowledge +
> personal instances) DONE + Phase 2 (document core / Persian OCR) DONE +
> Phase 3 (vision triage) DONE + Excel/DuckDB + video + note assistant.
> PostgreSQL 16 + pgvector schema with FORCED RLS (four session GUCs),
> migrations `0001`→`0011`, Redis/Celery/local-disk storage wiring,
> Next.js web app (30 routes: teacher + student + notes + admin).
> No containers: every service runs as a direct OS install.
> Flutter skeleton stays in `mobile/` and is DEFERRED until after the web app ships.

## 1. Mental model

```text
User
 ↓
Workspace            ← the tenant boundary (personal | shared)
 ├── Knowledge Bases → Source → Document → Page/Segment → Chunk
 │                     → Concept / Entity / Relation → Embedding
 │   (scope: workspace | global)
 ├── Notes / Files   (Sources of type note/url/audio/video/excel/csv, blobs in storage)
 ├── Memories        (user facts, goals, plans, summaries — NOT knowledge)
 ├── Assistants / Agents  (Definition → Instance; generic engine + registry)
 └── Conversations   (stateful, per agent + user)
```

Two top-level admin concepts sit above workspaces:

- **Agent Definitions** (`agent_definitions`): global, shared blueprints
  (instructions, tools, workflow, model_defaults, prompt_templates,
  safety_rules, output_format) — no workspace, no RLS, admin-gated.
- **Global Knowledge Bases** (`scope='global'`, `workspace_id IS NULL`):
  admin-created KBs inherited read-only by Definitions, never by a workspace.

## 2. Engines at a glance

| Engine | Owns | Vendor position |
|---|---|---|
| Knowledge Engine | `KnowledgeBase→Source→Document→PageSegment→Chunk→Concept/Relation→Embedding`; retrieval = vector + metadata filter + FTS + RRF + rerank seam (Qwen API via AvalAI, P4); workspace AND global (definition-assigned) variants | Structured extraction = Gemini Flash via GapGPT for PDFs (`app/knowledge/parsers/gemini_structure.py`, hybrid text-batches + vision for textless pages) + deterministic local parsers for the rest; Docling/RAG-Anything/EasyOCR were deleted; Source of Truth = our PostgreSQL + object storage |
| Memory Engine | user-related data: preferences, goals, facts, plans, decisions, per-instance summaries | `MemoryProvider` protocol; MVP = `PostgresMemoryProvider`. Zep is NOT used; a future adapter implements the same protocol |
| Agent Engine | generic `AgentDefinition` → `Agent` instance `{instructions,knowledge,memory,workflow,state,tools,model,safety}`; `Assistant` = simple, `Agent` = stateful w/ tools | LangGraph is an internal runtime (`app/agents/runtime/` only). Clients use `AgentService` |
| AI Provider | `generate / generate_structured / stream / embed` (+ `describe_image`, `transcribe_audio`, `generate_with_tools`); keys server-side; models from DB `ai_settings` + ENV | `openai_compat` (OpenAI **and** GapGPT via `OPENAI_COMPAT_BASE_URL`) + `gemini` (REST). `app/ai/factory.py` resolves the provider per purpose — swapping vendors is config, not code |
| Definitions + Global KB | agent definitions CRUD + canonical Definition↔Global-KB assignment + instance↔workspace-KB assignment | admin-gated routers (`/admin/agent-definitions`, `/admin/global-knowledge-bases`); RLS gated by `app.definition_id` GUC |
| Usage Ledger | `usage_events` spans (agent/retrieval/rerank/model/extraction) with tokens, latency, errors + static USD estimates; per-workspace/user aggregation for future subscription pricing (no billing logic) | internal only, no Langfuse; read surface `/admin/usage/summary` + `/workspaces/{id}/usage/summary` |

Conformance is enforced by `backend/tests/test_architecture.py`: vendor SDK
imports (raganything full-engine, lightrag, docling, langgraph, zep) outside
their adapter directories fail the suite.

### AI model configuration

Model settings live in the DB (`ai_settings`, admin-only
`GET/PUT /admin/ai-settings`) and override ENV:
resolution order = DB row → ENV/code default. Keys (unknown → 422):
`chat.default`, `embedding.default`, `audio.transcription`,
`agent.teacher_lesson_planner`, `agent.student_academic_coach`,
`agent.note_taking_assistant`, `retrieval.rerank`, `ocr.provider`,
`vision.provider`. Cheap live-verified defaults: chat `gpt-4o-mini`,
embedding `text-embedding-3-small` (1536d), audio `whisper-1` (+ Gemini
via GapGPT chat fallback), agents `gemini-2.5-flash` (chat + structured +
tool rounds, all GapGPT-routed). Per-agent provider/model/temperature/
max_tokens resolve as `chat.default ← agent.<key> ← definition.model_defaults`
(see `resolve_agent_chat`); prompt templates (lesson_plan/study_plan/
coach_chat/note_chat) live on the definition with built-in fallbacks.

## 3. Workspace + Global isolation (the most important rule)

No query, retrieval, file access, memory access or agent access runs without
a workspace context.

- **Application layer**: `app/core/workspace.py::require_workspace_id` /
  `scoped_filter` — every tenant-aware service takes an explicit
  `workspace_id`; missing context raises `MissingWorkspaceContextError`.
  `resolve_workspace` (workspaces/service.py) is the single dependency every
  `{workspace_id}` endpoint uses: it provisionally binds the claimed
  workspace, then membership-gates (404 for outsiders, no existence leak).
  Membership (`workspace_members`) is checked per request.
- **Database layer**: migrations `0001`→`0011` enable **FORCED** Row Level
  Security on all tenant tables. Final shape: **four session GUCs** —
  `app.workspace_id` (claimed workspace), `app.user_id` (JWT identity),
  `app.is_admin`, `app.definition_id` (agent definition for global-KB reads).
  Members see own rows + own workspaces (via subselect); admins bypass.
  Writes use `WITH CHECK`. `set_workspace_context` / `set_definition_context`
  bind the session and a SQLAlchemy `after_begin` listener re-applies
  `SET LOCAL` on every new transaction; `get_db` and the worker reset all
  GUCs on release. Celery tasks carry `workspace_id` in the message and bind
  before the first row access.
- **Global rows** (`workspace_id IS NULL`) are readable only when the current
  definition is assigned to that KB: policy =
  `workspace_id IS NULL AND EXISTS (SELECT 1 FROM agent_definition_knowledge
  WHERE kb_id = <row's kb> AND definition_id = app.definition_id)`
  (migration 0006, per-table joins, no placeholders). Intake for global KBs is
  admin-gated; the worker binds a transient admin context
  (`_bind_admin_read`) to read/write them.
- **Agents policy**: workspace gate AND (`owner_user_id IS NULL` OR
  `owner_user_id = app.user_id`) OR admin — app-layer 404 is primary, RLS is
  the second layer.
- **Tests**: `test_workspace_isolation.py` (app-layer + static policy
  verification), `test_rls_live.py` + `test_global_knowledge_live.py`
  (live PG, two tenants, definition-gated global read; skip without DB).

## 4. Knowledge design decisions

- One workspace → many knowledge bases → many sources (multi-source from day one).
- Source types: `pdf, docx, pptx, txt, md, image, audio, video, url, note,
  excel, csv`. Validation = extension allow-list + magic-byte sniffing +
  caps (50 MB files, 5 MB notes; video 100 MB / 20 min).
- `audio` (mp3/wav/m4a): transcription API only, route picked by the
  panel-editable `audio.transcription` model id (DB row wins over ENV
  `WHISPER_API_MODEL`): `gpt-4o-mini-transcribe` (DEFAULT) via AvalAI
  `/audio/transcriptions` (`json` text, no timestamps — live-verified),
  `whisper-*` via GapGPT `/audio/transcriptions` with timestamps, or
  Gemini-via-GapGPT chat with audio input → PageSegments with
  `start_ms/end_ms` → deterministic `chapterize()` (silence-gap سرفصل,
  NO diarization — titles + time ranges only) → ~45s-window chunks
  stamped with chapter section → transcript persisted with chapters →
  time-aware retrieval. 429/5xx retries honor Retry-After.
- `video` (mp4/webm/mov): **audio-track transcription only** via the
  imageio-ffmpeg static binary (no OS install, no frame analysis); guards
  `VIDEO_MAX_BYTES`/`VIDEO_MAX_DURATION_S` reject oversized/over-long clips.
- `url`: live fetch at ingest (15s timeout, 2MB cap, bs4 HTML→text) with a
  provider layer (`youtube` captions-first then audio fallback via yt-dlp,
  `instagram`, `aparat`, `generic`) returning text OR timestamped transcripts;
  descriptor fallback on any failure never fails the job.
- `excel`/`csv`: openpyxl parse (read-only) → a `.duckdb` artifact stored in
  StorageProvider next to the source blob + RAG summary chunks (structure +
  samples + detected inter-sheet relationships, never full rows);
  `detect_relations()` finds shared-column/value-overlap edges from
  samples (bounded, no model calls); the `excel_query` agent tool answers
  analytic questions with validated SELECT-only SQL (allowlist
  tables/columns, 2-table INNER JOIN only on detected edges, row/time
  caps, deterministic keyword SQL incl. cross-table JOIN + LLM fallback).
- Page numbers (documents) and timestamps (audio/video) are preserved on
  segments and propagated to chunks/citations.
- Raw files → local disk (StorageProvider; S3 implements the same protocol
  later); PostgreSQL → metadata + structure + embeddings. Storage keys are
  namespaced per workspace (`workspaces/{ws}/kb/{kb}/...`) and per global KB
  (`global/kb/{kb}/...`) — no cross-tenant key guessing.
- `scope` on KBs: `workspace` (workspace_id NOT NULL) | `global`
  (workspace_id NULL), enforced by CHECK `ck_kb_scope_ws`.
- Embeddings: provider-swappable; model + dim from `embedding.default`
  (DB row wins over ENV); `chunks.embedding` is `VECTOR(1536)` with an HNSW
  cosine index. Changing the model later needs a re-index migration.
- `sources.parse_meta` (JSONB) records parse/OCR/vision provenance per
  source for the admin pipeline UX (no re-parse).

## 5. Processing pipeline (worker core DONE)

```text
Upload → Storage → Source(row, pending) → Celery ingest_source task
→ parse → structure → chunk → embed → PostgreSQL → ready/failed
```

`run_ingest` (workers/tasks.py, injectable deps): download → `parse_source`
→ Document + PageSegments → word-chunks (400w/60 overlap, page + timestamp
preserving; audio/video merged in ~45s windows) → embeddings (batched, 32)
→ `content_tsv` backfill → `ready`; any exception → `failed` with message,
never a crash. Re-ingest deletes the previous tree first (no duplicates).

- Parsers: PDFs go through Gemini structured extraction first
  (`USE_GEMINI_PDF`, `GEMINI_STRUCT_MODEL` via GapGPT; text batches of
  `GEMINI_STRUCT_PAGES_PER_CALL` pages + vision for textless pages;
  headings/sections/tables/order/page numbers preserved for chunking),
  then flat pypdf fallback on any failure. docx (tables in order) /
  pptx / text / url-provider / note / image-meta parse locally.
- OCR (Persian, admin-selected `ocr.provider`): `gemini` (pypdfium2
  rasterize + GapGPT-routed describe_image; no Google key) or `disabled`
  (SAFE DEFAULT). Blank PDF pages are refilled; any degraded-OCR document
  that yields zero chunks is a loud FAILURE, never a green empty ingest.
  Every model call reports token usage into `parse_meta` (feeds the P3
  usage ledger for per-user/workspace billing).
- Vision (Phase 3, `DOCUMENT_VISION_ENABLED`): figure triage (caption already
  data-carrying / page already text-rich / over-cap skip), Gemini describe
  with strict no-confabulation rules (`vision_uncertain` flag), figure blobs
  stored per source; chunk content = draft text + vision description,
  embedding runs AFTER vision. Dense/small figures the model can't read keep
  our placeholder, flagged degraded.
- Excel/CSV: `.duckdb` artifact built in-worker and stored; summary pages
  flow through the normal RAG pipeline unchanged.
- Global sources (`workspace_id IS NULL`): intake is admin-gated at the API;
  the worker binds a transient admin context for read/write; retrieval stays
  gated by `agent_definition_knowledge` assignment.

## 6. Retrieval (DONE)

Two entry points share one algorithm (vector + FTS + titles → RRF →
heuristic rerank → passages):

- `hybrid_search` (workspace rows): pgvector cosine + `kb_id/workspace_id`
  filter + FTS (`to_tsvector('simple')` over Persian-normalized text) +
  title stream (document-title/filename ILIKE recall) → RRF(k=60) →
  title boost → rerank (`retrieval.rerank` in ai_settings; heuristic
  term-coverage + title overlap today, cross-encoder contract tomorrow;
  disable with `{"enabled": false}`).
- `hybrid_search_global` (definition-assigned global rows only; empty kb_ids
  → `[]`, RLS independently denies unassigned rows): the SAME pipeline,
  symmetric rerank applied by default. Callers pass only KBs assigned to the
  current definition (never user input).
- Persian normalization (`app/knowledge/normalize.py`, mirrored by migration
  0017 backfill): ي/ك/ة → Persian, tashkeel stripped, ZWNJ → space — applied
  at ingest (chunk content, doc titles), to queries, and to FTS/rerank
  matching, so Arabic-keyboard variants always hit.
- Passages (`retrieval/passages.py`): hits merge with neighboring chunks
  into coherent capped passages (citations stay chunk-level); when one
  source dominates (or the user names the article), its full extracted
  text is injected as an extra context block (capped).

`content_tsv` is backfilled at ingest time on Postgres. Endpoints:
`POST /workspaces/{id}/knowledge/search` (any member) and
`POST /workspaces/{id}/knowledge/debug` (owner/admin only) returning the full
trace `query → chunks+scores+ranks → context → answer` with the effective
chat model plus `retrieval_ms`/`generation_ms`. Unit tests run the same
fusion code on SQLite (pure-Python cosine + LIKE fallback).

Intake routes (`/workspaces/{id}/knowledge-bases…/sources`, and admin
`/admin/global-knowledge-bases/{kb}/sources`): multipart file or `/link` for
url/note. Files are never public: downloads stream through the authenticated
`…/sources/{id}/download` endpoint; presigned URLs exist only for the s3
backend. `enqueue_ingest` pre-checks the broker over TCP (1s) so uploads never
hang when Redis is down — sources stay `pending` for retry.

## 7. Memory engine (DONE)

`MemoryProvider` protocol; MVP = `PostgresMemoryProvider` (`memory_facts`,
`conversation_summaries`, scoped to (workspace, user)). `remember` upserts by
(workspace, user, key); `recall` is token-overlap scoring with category boosts
(goal/plan/preference); `summarize` upserts per scope.

Scoping (locked): the generic agent engine writes rolling summaries
**per instance** (`agent_instance:{id}`); the legacy scope `agent:{key}` is
read ONLY as a fallback for definition-less instances. Durable facts are
written ONLY via explicit profile/plan endpoints — no LLM auto-facts.
Endpoints are workspace-nested
(`/workspaces/{id}/memory/facts|search|summaries`); reading another user's
memory needs owner/admin workspace role. A vector upgrade can replace the
scorer without changing callers.

## 8. Agent engine (DONE)

- **Definition** (global, admin) → **Instance** (workspace-scoped, optional
  `owner_user_id` for personal instances; one personal instance per
  workspace/definition/user via partial unique index; `definition_id` FK is
  ON DELETE RESTRICT so a definition can never silently orphan instances).
- `AGENT_CATALOG` stays as the legacy fallback seed; the DB-backed definition
  is the primary source. Tools come from `definition.tools`:
  teacher = knowledge_search + excel_query; student = knowledge_search +
  memory_search + excel_query; note = knowledge_search + memory_search +
  excel_query. Tools are plain workspace-scoped functions
  (`knowledge_search`, `global_knowledge_search`, `memory_search`,
  `memory_facts`, `excel_query`) plus two CLIENT-executed tools
  (`web_search`, `maps_search` — the LLM routes via function-calling, the
  USER'S BROWSER executes via Overpass/DDG specs, the server only
  validates via `/chat/resume`; this server never searches the web itself
  unless a `side="server"` provider is explicitly selected + keyed).
- **Search provider chains** (`app/ai/web_providers.py`,
  `app/ai/places_providers.py`): `Provider` interface + ordered
  `ProviderChain(primary, *fallbacks)` with per-provider timeout and
  `served_by` audit. Tools depend only on the interface — DDG/Overpass
  today, Gemini grounding / Google Places / GapGPT tomorrow, no tool
  or runtime changes. Config: `WEB_SEARCH_PROVIDER/_FALLBACKS/_SIDE`,
  `PLACES_PROVIDER/_FALLBACKS` (default client-only).
- Effective instructions = definition.instructions + `custom_instructions`
  (config legacy fallback only for definition-less rows).
- **State split (never mixed)**: `agents.runtime_state` = instance-level
  (current_lesson/plan/progress/preferences); `conversations.state` =
  per-conversation/turn (turns/last_kb_id/kind/drafts).
- **Runtime**: LangGraph `StateGraph` — linear
  `load_memory → retrieve_knowledge → build_prompt → llm_call` (internal to
  `app/agents/runtime/`; clients use `AgentService` only).
  `retrieve_knowledge` merges workspace + assigned-global + excel chunks
  (de-duped by chunk_id, workspace-first ordering).
- **Streaming**: `…/chat/stream` (generic) and `…/coach/chat/stream`
  (student) emit SSE frames `meta → token* → done|error → [DONE]` reusing the
  same graph nodes; persistence happens once after the stream.
  Client-tool turns emit `meta → tool_call+` and pause (max 2 rounds/turn);
  the browser POSTs results to `…/chat/resume`, which validates, stores
  leads, runs the server-side chain remainder on browser failure, and
  streams the final answer. Citations: `[W]` workspace, `[G]` global,
  `[X]` excel, `[S]` web, `[M]` maps.
- **Leads** (`leads` table, migration 0012, workspace RLS): maps_search
  results persist as rows (query/name/address/phone/hours/website/lat/lng/
  source/raw/status); export (CSV/Excel) and KB-import materialize FROM the
  DB. Endpoints: `GET …/leads`, `PATCH …/leads/status`,
  `GET …/leads/export?format=csv|xlsx`,
  `POST …/knowledge-bases/{kb}/leads/import`.
- **Safety** (structural, no fake AI firewall): `MAX_MESSAGE_CHARS`=4000,
  `MAX_HISTORY_TURNS`=12, context/instruction separation + `SAFETY_PREAMBLE`,
  injection detection audit-logged (`agents.injection_attempt`), linear graph
  (step cap by design), workspace-scoped tools, per-agent tool allow-lists.

## 9. Product agents (DONE)

- **Teacher** (`teacher_lesson_planner`, `app/agents/teacher/`): kb +
  chapter/grade/duration/style/instructions → `hybrid_search` → Persian
  prompt (`lesson_plan` template) → `generate_structured` (JSON-mode,
  Pydantic-validated) → references attached from REAL chunks in code (page/
  timestamp preserved, never LLM-hallucinated) → saved BOTH as a
  `lesson_plans` structured row (migration 0003, tenant RLS — the queryable
  Source of Truth for list/detail views) AND as conversation messages.
  Endpoints: POST/GET `…/lesson-plans`, GET `…/lesson-plans/{id}`,
  GET `/workspaces/{id}/lesson-plans/by-conversation/{cid}`. Wrong agent key
  → 422; cross-workspace/user → 404.
- **Student** (`student_academic_coach`, `app/agents/student/`): profile
  (`profile.*` memory facts), durable `goal.*` facts on plan creation,
  structured study plans (weeks/tasks with service-stamped ids,
  `…/study-plans`), stateful `…/coach/chat` (profile + plan + progress +
  history + optional KB in every prompt; rolling summary per turn), coach
  tools `get_study_plan`/`create_study_plan`/`update_progress` in an agentic
  loop (≤3 rounds, self-correcting on tool errors), and `…/progress` with
  task-id validation against the stored plan.
- **Note** (`note_taking_assistant`, `app/agents/note/`): answers from the
  user's files/notes/links with `[W1]`/`[G1]`/`[X1]` citation paths — thin by
  design, runs entirely through the generic engine.

## 10. API surface (ours — not Dify, not any vendor)

- Public: `/health`, `/version`, `/ai/providers`, `/auth/register|login|refresh|logout`.
- Auth: `/auth/me` (any authenticated user).
- Admin-only (`require_admin`): `/admin/ai-settings` (GET/PUT),
  `/admin/audit-logs`, `/admin/users` (list + is_admin toggle),
  `/admin/agent-definitions` (CRUD + `/{id}/effective-config` + `/{id}/knowledge`
  global-KB assignment + `/{id}/instances` + `prompts/defaults`),
  `/admin/global-knowledge-bases` (CRUD + sources upload/link + transcript +
  processing).
- Workspace-nested (`resolve_workspace` member-gated): workspaces CRUD +
   members (owner/admin to add), knowledge-bases CRUD, sources (upload/link/
   list/processing/transcript/retry/download/download-url/delete — delete
   purges the full RAG footprint: ingest tree with embeddings, figure/duckdb
   blobs, source blob), `PUT .../sources/{id}/text` (manual text edit +
   re-index), `POST .../sources/{id}/revise` (AI re-extraction preview), `knowledge/search`
  (any member), `knowledge/debug` (owner/admin), memory facts/search/summaries
  (owner/admin to read another user), agents CRUD + `knowledge` (instance
   workspace-KB assignment, global read-only) + `state` + `chat` + `chat/stream`
   + `chat/resume` (client-tool continuation) + `leads` (list/status/export +
   KB-import) + `conversations` + messages, teacher lesson-plans, student profile/
  study-plans/coach chat (+stream)/progress.
- Rate limits (in-process sliding window; Redis swap documented): login
  10/min per IP+email, refresh 30/min, chat 60/min per user, plans 20/min,
  debug 30/min.

## 11. Security (DONE unless noted)

Auth: email+password (bcrypt, 8–72 chars), JWT access (15m) + opaque refresh
tokens (SHA-256 hashes in `refresh_tokens`, rotation on every refresh, 30d
expiry). RBAC: `is_admin` + `require_admin`; workspace roles owner/admin/member
(`require_workspace_role`); personal-agent visibility = shared OR owner OR
admin (404 for everyone else, no existence leak). Login failures + logins +
refreshes + chat/lesson-plan/coach/profile/coach-tool actions are audit-logged
(`audit_logs`, PII-redacted). Prompt-injection: length caps + context/
instruction separation preamble; attempts are audit-logged, never silently
trusted. Intentionally global tables (no RLS, admin/app-gated): `users`,
`refresh_tokens`, `audit_logs`, `ai_settings`, `agent_definitions`,
`agent_definition_knowledge`. RLS-gated tenant tables: knowledge chain (7),
memory (2), agents/conversations/messages/lesson_plans (4), workspaces +
members + agent_kb_assignments. Still to come: Redis-backed rate limiting,
prompt-injection stripping, private buckets + signed URLs (S3 backend).

## 12. Runtime topology (direct installs, no containers)

```text
PostgreSQL 16 + pgvector :5432 ─┐
Redis :6379 ────────────────────├── FastAPI (uvicorn :8000, .venv)
Local disk storage ─────────────├── Celery worker (same .venv, separate process)
  (data/storage; incl. .duckdb   Next.js web app :3000 (teacher + student
   artifacts + figure blobs)     + notes + admin)
                                 Flutter: DEFERRED (skeleton only)
```

(No MinIO: upstream archived open-source builds; MVP stores files on server
disk behind the StorageProvider abstraction. No local transcription models;
heavy optional deps — docling/easyocr — only load when selected.)

## 13. Repo layout

```text
allmai/
├── ARCHITECTURE.md  README.md  .env.example  .gitignore  .venv/
├── backend/
│   ├── app/  core{auth,workspace,database,config,security}  common{audit,rate_limit,pii}
│   │         users  workspaces  auth  admin  storage  ai{openai_compat,gemini,embedding}
│   │         knowledge{parsers(+providers,excel),retrieval,excel}
│   │         memory  conversations  agents{registry,definitions_*,runtime,state,tools,
│   │                                 safety,teacher,student,note}
│   ├── workers/ (celery_app, tasks)   alembic/ (0001→0011)   tests/ (32 files)
├── web/      (Next.js + TS + Tailwind, RTL — teacher + student + notes + admin)
├── mobile/   (Flutter skeleton, DEFERRED)
└── infra/    (postgres_init.sql, service setup notes — no compose files)
```

## Web app routes (Next.js, 30 routes)

```text
/                     landing
/teacher              home + assistants
/teacher/lesson-plans new plan (KB, chapter, grade, duration, style)
/teacher/lesson-plans/[id]  plan detail (from structured lesson_plans row)
/teacher/chat         free AI interaction
/student              home + coach profile
/student/plan         study plan (from coach conversation state)
/student/progress     progress view
/student/chat         coach chat
/notes                notes assistant hub (create note agent)
/notes/files          file/note/link uploader
/notes/chat           streaming notes chat
/extraction           data-extraction app (chat tab ChatGPT-like with inline
                      lead files + results tab: lead table, CSV/Excel export,
                      save-to-KB; uses data_extraction_assistant instance)
/guidance             career-guidance app (chat + RIASEC personality quiz +
                      O_NET job matching; uses moshaver instance; deterministic
                      DuckDB match in app/agents/guidance.py)
/admin/*              dashboard, users, workspaces, knowledge-bases,
                      global-knowledge, sources, documents, processing,
                      debug, ai-settings, agents, conversations,
                      agent-studio (list + [id] editor), login
```

All pages are client-rendered (App Router), RTL/Persian; localStorage holds
only tokens + UI state; every data call goes to the FastAPI API
(`NEXT_PUBLIC_API_BASE`, default `http://127.0.0.1:8000`) with silent token
refresh.

## 14. Phase order

0. Setup (DONE) → 1. Auth (DONE) → 2. Workspace + membership + live RLS
(DONE) → 3. Storage + KB/Source APIs (DONE) → 4. Ingestion pipeline (DONE) →
5. Retrieval + debugger (DONE) → 6. Memory engine (DONE) → 7. Agent engine +
LangGraph (DONE) → 8. Teacher agent (DONE) → 9. Student agent (DONE) →
 10. Admin web (DONE) → 11. Web app (DONE) → 12. Audit + production readiness
 (DONE) → Phase 1: Agent Definitions + Global Knowledge + personal instances
 (DONE) → Phase 2: document core / Persian OCR (DONE) → Phase 3: vision triage
 (DONE) → Excel/DuckDB + video + note assistant (DONE) →
 Gemini structured extraction, Docling deleted (DONE) →
 P2-light retrieval baseline (DONE: Recall@8=1.0, MRR=0.86, `eval/
 golden-light.jsonl` + `eval/baseline-light.report.json`) →
 P3 usage ledger (DONE: `usage_events` + per-span recording + pricing
 estimates, no billing) → P4 Qwen reranker eval (DONE: api/avalai
 qwen3-rerank stays the production default; MRR 0.86→1.0, 0 errors,
 `eval/rerank-qwen.report.json`; local reranker NOT built per scope) →
 next: P5 media hardening (Redis rate limits, S3 backend) →
 P5 media (DONE: structured image describe, audio سرفصل without
 diarization, Excel relations + JOIN) → hardening + ops.
Each phase ends with `pytest` green; E2E: workspace→KB→sources→search→
agent→chat with zero cross-workspace leakage.

## 15. E2E proof

 `backend/tests/test_e2e.py` runs the full chain in one test with mocked AI +
 fake storage: register → workspace → KB → note source → real ingest →
 search → teacher agent (plan + chat with grounded citations) → student agent
 (profile → plan → chat → progress) → outsider gets 404 on
 knowledge/search/chat/memory. Backend suite green (`pytest`; 332 collected
 tests across 43 files; live-AI + live-RLS + live-global-KB tests skip
 without services/flags): auth/RBAC/audit, workspace isolation (app-layer +
 static RLS), file
 validation, ingest (Gemini-structured PDF with fakes + real PDF/audio/
 video/excel paths), hybrid retrieval + rerank seam, usage ledger
 (spans/pricing/summaries + live RLS proof), structured image describe,
 audio chapters without diarization, Excel relations + JOIN, memory,
 agents (chat, lesson plans, coach, progress, definitions, notes), URL
 providers, Excel/DuckDB, OCR/vision seams, security (IDOR,
 expired-token, frontend-hygiene), full E2E. `npm run build` green on
 30 web routes.
```