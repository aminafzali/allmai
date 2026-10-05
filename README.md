# AllMai AI Platform

Extensible AI platform (FastAPI + Celery + PostgreSQL/pgvector + Redis +
local-disk storage, Next.js web app for teacher/student/notes/admin).
**No containers**: all services run as direct OS installs. See
`ARCHITECTURE.md` for the full design.
Flutter is out of scope (`mobile/` is an untouched skeleton for later).

## 1. Prerequisites (install directly on the OS)

| Service | Install |
|---|---|
| PostgreSQL 16 | Any Windows build (EDB/Odoo bundle OK). pgvector needed: if your installer lacks `vector`, extract `vector.dll` + `share/postgresql/extension/vector*` from the `pgserver` PyPI wheel (ships PG16 binaries) into PG `lib/` + `share/extension/`, then `CREATE EXTENSION vector;`. Then run `infra/postgres_init.sql` via psql (or create role/db as in section 2) |
| Redis 7+ | `winget install Redis.Redis` (MSOpenTech port, runs as a service on :6379). Note: backend pins `redis==5.0.1` (RESP2) for compatibility |
| File storage | None to install — MVP uses server-local disk (`STORAGE_BACKEND=local`, dir `STORAGE_LOCAL_DIR`). (MinIO open-source is archived upstream and not used.) |
| Python 3.12 | python.org. Backend uses the repo-local venv at `.venv/` |
| Node.js 20+ | nodejs.org (for `web/`) |

## 2. Backend setup

```bat
copy .env.example .env
REM edit .env : DATABASE_URL, REDIS_URL, S3 keys, OPENAI_COMPAT_API_KEY (GapGPT) ...

.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

REM create schema (needs PostgreSQL running + infra\postgres_init.sql applied once):
cd backend
..\.venv\Scripts\python.exe -m alembic upgrade head
REM check chain: ..\.venv\Scripts\python.exe -m alembic history
REM without a live DB you can still validate the migration SQL offline:
..\.venv\Scripts\python.exe -m alembic upgrade head --sql
```

## 3. Run (all services, each its own process)

```bat
REM one-click (PostgreSQL + Redis must already run):
scripts\start-all.bat
REM stop: scripts\stop-all.bat
```

Manual equivalent:

```bat
REM 1. PostgreSQL 16 service (must be Running) + 2. Redis on :6379
REM 3. API (from backend\)
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

REM 4. Worker (from backend\)
..\.venv\Scripts\celery.exe -A workers.celery_app.celery worker --loglevel=info --pool=solo
REM The worker consumes `ingest_source`: parse -> chunk -> embed -> ready.
REM Needs PostgreSQL + AI key; without Redis, uploads stay `pending`.

REM 5. Web (from web\, port 3001 if 3000 is taken)
npm install
npm run dev -- --port 3001   REM http://localhost:3001  (NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000)
```

Health check: `http://127.0.0.1:8000/health`.

Auth quickstart (see Phase 1):

```bat
curl -X POST 127.0.0.1:8000/auth/register -H "Content-Type: application/json" -d "{\"email\":\"u@x.com\",\"password\":\"secret123\"}"
curl -X POST 127.0.0.1:8000/auth/login    -H "Content-Type: application/json" -d "{\"email\":\"u@x.com\",\"password\":\"secret123\"}"
REM -> {access_token, refresh_token}; use Authorization: Bearer <access> for /auth/me
```

Admin model settings (needs `is_admin=1` user; flip the flag directly in DB
for the first admin): `GET /admin/ai-settings`, `PUT /admin/ai-settings`
`{"key":"chat.default","value":{"model":"gpt-4.1-nano"}}`, `GET /admin/audit-logs`.
First-run cheap defaults are seeded by migration `0002_auth_aux`.

Workspaces (all need `Authorization: Bearer <access>`):

```bat
curl -X POST 127.0.0.1:8000/workspaces -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d "{\"name\":\"My class\",\"type\":\"shared\"}"
curl 127.0.0.1:8000/workspaces -H "Authorization: Bearer <access>"
curl -X POST 127.0.0.1:8000/workspaces/<id>/members -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d "{\"user_id\":\"<uuid>\",\"role\":\"member\"}"
```

Live RLS proof (needs PostgreSQL running + migrated): `pytest tests/test_rls_live.py`.
Without a DB it skips; the app-layer isolation suite always runs.

Knowledge quickstart (member of workspace `<wid>`, KB `<kb>`):

```bat
curl -X POST 127.0.0.1:8000/workspaces/<wid>/knowledge-bases -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d "{\"title\":\"Chemistry\"}"
curl -X POST 127.0.0.1:8000/workspaces/<wid>/knowledge-bases/<kb>/sources -H "Authorization: Bearer <access>" -F "type=pdf" -F "file=@textbook.pdf"
curl -X POST 127.0.0.1:8000/workspaces/<wid>/knowledge-bases/<kb>/sources/link -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d "{\"type\":\"note\",\"title\":\"N\",\"content\":\"...\"}"
curl 127.0.0.1:8000/workspaces/<wid>/sources/<sid>/download-url -H "Authorization: Bearer <access>"

Search + retrieval debugger (owner/admin for debug):

```bat
curl -X POST 127.0.0.1:8000/workspaces/<wid>/knowledge/search -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d "{\"query\":\"acids\",\"top_k\":5}"
curl -X POST 127.0.0.1:8000/workspaces/<wid>/knowledge/debug -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d "{\"query\":\"what are acids?\"}"

Memory (same auth header):

```bat
curl -X POST 127.0.0.1:8000/workspaces/<wid>/memory/facts -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d "{\"key\":\"goal\",\"value\":\"master calculus\",\"category\":\"goal\"}"
curl -X POST 127.0.0.1:8000/workspaces/<wid>/memory/search -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d "{\"query\":\"calculus goal\"}"
```

Agents (same auth header; `<aid>` = agent id, `<kb>` optional):

```bat
curl -X POST 127.0.0.1:8000/workspaces/<wid>/agents -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d "{\"key\":\"teacher_lesson_planner\",\"name\":\"Planner\"}"
curl -X POST 127.0.0.1:8000/workspaces/<wid>/agents/<aid>/chat -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d "{\"message\":\"Explain acids\",\"kb_id\":\"<kb>\"}"
REM streaming (SSE: meta -> token* -> done, then [DONE]):
curl -N -X POST 127.0.0.1:8000/workspaces/<wid>/agents/<aid>/chat/stream -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d "{\"message\":\"Explain acids\",\"kb_id\":\"<kb>\"}"
curl -N -X POST 127.0.0.1:8000/workspaces/<wid>/agents/<aid>/coach/chat/stream -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d "{\"message\":\"What next?\"}"

Teacher lesson plan (agent key must be `teacher_lesson_planner`):

```bat
curl -X POST 127.0.0.1:8000/workspaces/<wid>/agents/<aid>/lesson-plans -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d "{\"kb_id\":\"<kb>\",\"chapter\":\"Acids and bases\",\"grade\":\"9th\",\"duration_minutes\":60,\"teaching_style\":\"group work\",\"instructions\":\"Focus on lab safety.\"}"

Student coach (agent key must be `student_academic_coach`):

```bat
curl -X POST 127.0.0.1:8000/workspaces/<wid>/agents/<aid>/profile -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d "{\"facts\":{\"grade\":\"9th\"}}"
curl -X POST 127.0.0.1:8000/workspaces/<wid>/agents/<aid>/study-plans -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d "{\"goals\":[\"Pass algebra\"],\"weekly_hours\":6}"
curl -X POST 127.0.0.1:8000/workspaces/<wid>/agents/<aid>/coach/chat -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d "{\"message\":\"What next?\",\"conversation_id\":\"<cid>\"}"
curl -X POST 127.0.0.1:8000/workspaces/<wid>/agents/<aid>/progress -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d "{\"conversation_id\":\"<cid>\",\"completed_task_ids\":[\"w1t1\"]}"
```

Lesson-plan reads (structured rows, not chat text):

```bat
curl 127.0.0.1:8000/workspaces/<wid>/agents/<aid>/lesson-plans -H "Authorization: Bearer <access>"
curl 127.0.0.1:8000/workspaces/<wid>/lesson-plans/by-conversation/<cid> -H "Authorization: Bearer <access>"
```

Lead mining (enable `web_search`/`maps_search` per agent definition in
Agent Studio first; both default OFF. Search executes in the USER'S
BROWSER via `/chat/stream` -> `tool_call` SSE -> browser -> `/chat/resume`;
this server never searches itself. Leads persist in the `leads` table):

```bat
curl 127.0.0.1:8000/workspaces/<wid>/leads -H "Authorization: Bearer <access>"
curl 127.0.0.1:8000/workspaces/<wid>/leads/export?format=xlsx -H "Authorization: Bearer <access>" --output leads.xlsx
curl -X POST 127.0.0.1:8000/workspaces/<wid>/knowledge-bases/<kb>/leads/import -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d "{\"lead_ids\":[\"<lid>\"]}"
```

## 4. Tests

```bat
cd backend
..\.venv\Scripts\python.exe -m pytest -q
REM live AI (real provider calls, tiny cost): set RUN_LIVE_AI=1 first
REM live Postgres RLS: pytest tests/test_rls_live.py  (needs running DB)
```

Default suite is offline (mocked AI + fake storage + SQLite): auth/RBAC/audit,
workspace isolation (app-layer + static RLS verification), file validation,
ingest (Gemini-structured PDF with fakes + real PDF/audio/video/excel paths),
hybrid retrieval + rerank seam, usage ledger (spans/pricing/summaries),
structured image describe, audio chapters without diarization, Excel
relations + JOIN, memory, agents (chat, lesson plans, coach, progress,
definitions, notes), URL providers (YouTube/Instagram/Aparat),
Excel/DuckDB, OCR/vision seams, IDOR/expired-token/frontend-hygiene
security tests, full E2E flow. No live Postgres/Redis/MinIO required.

## 5. Web (Next.js: admin + teacher + student + notes) / Mobile (deferred)

```bat
cd web
npm install
npm run dev      REM http://localhost:3000 — login at /, then /teacher, /student, /notes, /admin
npm run build    REM production validation (30 routes)
```

Admin panel (all under `/admin`, login required, admin-gated data):
users (list + admin toggle), workspaces (create/list), knowledge-bases
(ws-scoped CRUD), global-knowledge (admin KBs + assign to agent
definitions), sources (upload/note/url + authenticated download),
documents (per-source pipeline stages: parse/OCR/vision/chunking + figures),
processing (status counts), agents (create + in-panel chat test with
citations), conversations (per-agent history), agent-studio (agent
definition editor: instructions/tools (incl. global-KB search, web search,
maps search toggles)/model/prompts/safety/knowledge),
debug (full query→chunks→context→answer trace), ai-settings (live model
editing). API base override: `NEXT_PUBLIC_API_BASE`.

Teacher flows (`/teacher`): home (workspace + assistants), lesson-plans
(list from structured rows → detail from the structured endpoint with
references), lesson-plans/new (KB/chapter/grade/duration/style → plan),
chat (KB-grounded, conversation continuity, lead table + CSV/Excel export
when the definition enables web/maps search). Student flows
(`/student`): home + academic profile, coach chat (persistent conversation),
study plan (create + latest-plan view), progress (task checkboxes +
notes). Notes flows (`/notes`): notes assistant hub (create agent), files
(upload files/notes/links to a KB), chat (streaming, KB-grounded).
Extraction flows (`/extraction`): dedicated data-extraction app with chat
tab (ChatGPT-like, inline lead files per conversation) + results tab
(lead table, CSV/Excel download, save-to-KB); backed by the
`data_extraction_assistant` definition (web/maps tools, browser-executed).
Guidance flows (`/guidance`): career-counselor app with chat tab,
RIASEC personality-quiz tab (saved to memory profile) and jobs tab
(deterministic O_NET match); backed by the `moshaver` definition.
Auth guard + RTL throughout. localStorage holds only UI state
(tokens, last workspace, active conversation); all data comes from the API.

`mobile/` is a Flutter skeleton kept for later — the web app ships first
and covers all teacher/student/notes flows (see ARCHITECTURE.md § repo layout).

## 6. Configuration

Everything comes from root `.env` (see `.env.example`): DB/Redis/S3 endpoints,
JWT, `AI_PROVIDER`, `OPENAI_COMPAT_BASE_URL` (default GapGPT
`https://api.gapgpt.app/v1`, swap to `https://api.openai.com/v1` for OpenAI),
`CHAT_MODEL`, Gemini key/model, `EMBEDDING_MODEL/DIM`, Whisper settings,
 video guards (`VIDEO_MAX_BYTES`/`VIDEO_MAX_DURATION_S`), Excel/CSV caps
 (`EXCEL_MAX_ROWS_PER_SHEET`, `EXCEL_QUERY_*`), structured PDF extraction
 (`USE_GEMINI_PDF`/`GEMINI_STRUCT_MODEL`/`GEMINI_STRUCT_PAGES_PER_CALL`,
 GapGPT-routed Gemini; Docling deleted), and vision
 (`DOCUMENT_VISION_ENABLED` + `VISION_*`). Extraction/OCR/Vision
 provider+model selection lives in the DB (`ai_settings` keys
 `extraction.pdf`/`ocr.provider`/`vision.provider`), editable in
 `/admin/ai-settings`. Search backends are swappable provider chains
(`WEB_SEARCH_PROVIDER/_FALLBACKS/_SIDE`, `PLACES_PROVIDER/_FALLBACKS`;
default client-only, so the server never searches itself). API keys stay
server-side; `GET /ai/providers` only exposes names.
