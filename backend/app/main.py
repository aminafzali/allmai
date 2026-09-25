"""FastAPI application: one modular monolith (no microservices)."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.admin.router import router as admin_router
from app.agents.definitions_router import global_kb_router, router as definitions_router
from app.agents.router import router as agents_router
from app.agents.student.router import router as student_router
from app.agents.teacher.router import router as teacher_router
from app.ai.router import router as ai_router
from app.auth.router import router as auth_router
from app.conversations.router import router as conversations_router
from app.core.config import get_settings
from app.knowledge.router import router as knowledge_router
from app.memory.router import router as memory_router
from app.users.router import router as users_router
from app.workspaces.router import router as workspaces_router

settings = get_settings()

app = FastAPI(title=settings.APP_NAME, version="0.1.0")

# Browser access: the Next.js app (and only the configured origins) may
# call this API cross-origin. Tighten CORS_ORIGINS in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "env": settings.ENV}


@app.get("/version")
def version() -> dict:
    return {"app": settings.APP_NAME, "version": app.version}


app.include_router(auth_router)
app.include_router(users_router)
app.include_router(admin_router)
app.include_router(definitions_router)
app.include_router(global_kb_router)
app.include_router(workspaces_router)
app.include_router(knowledge_router)
app.include_router(memory_router)
app.include_router(agents_router)
app.include_router(teacher_router)
app.include_router(student_router)
app.include_router(conversations_router)
app.include_router(ai_router)
