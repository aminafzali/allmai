from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.agents import schemas
from app.agents.service import (
    chat as service_chat,
    chat_resume_events,
    patch_conversation,
)
from app.agents.service import (
    chat_stream_events,
    create_agent,
    get_agent,
    get_messages,
    list_agents,
    list_conversations,
    update_agent,
)
from app.agents.sse import sse_response
from app.auth.service import get_current_user
from app.core.database import get_db
from app.knowledge.retrieval.hybrid import get_embed_fn, get_generate_fn, get_stream_fn
from app.storage.s3 import get_storage
from app.users.models import User
from app.workspaces.models import Workspace
from app.workspaces.service import resolve_workspace

router = APIRouter(tags=["agents"])


@router.post("/workspaces/{workspace_id}/agents",
             response_model=schemas.AgentOut, status_code=201)
def post_agent(
    body: schemas.AgentCreate,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return create_agent(db, ws, user, body.key, body.type, body.name, body.config,
                        definition_id=body.definition_id,
                        owner_user_id=body.owner_user_id,
                        custom_instructions=body.custom_instructions,
                        kb_ids=body.kb_ids)


@router.get("/workspaces/{workspace_id}/agents",
            response_model=list[schemas.AgentOut])
def get_agents(ws: Workspace = Depends(resolve_workspace),
               db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    return list_agents(db, ws, user)


@router.get("/workspaces/{workspace_id}/agents/{agent_id}",
            response_model=schemas.AgentOut)
def get_agent_one(agent_id: str,
                  ws: Workspace = Depends(resolve_workspace), db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    return get_agent(db, ws, agent_id, user)


@router.patch("/workspaces/{workspace_id}/agents/{agent_id}",
              response_model=schemas.AgentOut)
def patch_agent(agent_id: str, body: schemas.AgentUpdate,
                ws: Workspace = Depends(resolve_workspace),
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    agent = get_agent(db, ws, agent_id, user)
    return update_agent(db, ws, agent, user, body.model_dump(exclude_unset=True))


@router.get("/workspaces/{workspace_id}/agents/{agent_id}/knowledge")
def get_agent_knowledge(agent_id: str,
                        ws: Workspace = Depends(resolve_workspace),
                        db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    from app.agents.definitions_service import knowledge_view

    agent = get_agent(db, ws, agent_id, user)
    view = knowledge_view(db, agent)
    # Locked UX: inherited global is read-only; instance manages workspace KBs.
    return {"global_inherited_readonly": view["global"],
            "workspace": view["workspace"]}


@router.put("/workspaces/{workspace_id}/agents/{agent_id}/knowledge")
def put_agent_knowledge(agent_id: str, body: schemas.DefinitionKnowledgeUpdate,
                        ws: Workspace = Depends(resolve_workspace),
                        db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    from app.agents.definitions_service import instance_kb_ids, set_instance_knowledge

    agent = get_agent(db, ws, agent_id, user)
    kids = set_instance_knowledge(db, ws, agent, body.kb_ids)
    return {"kb_ids": [str(k) for k in kids],
            "note": "workspace KBs only; global is inherited read-only"}


@router.get("/workspaces/{workspace_id}/agents/{agent_id}/state")
def get_agent_state(agent_id: str,
                    ws: Workspace = Depends(resolve_workspace),
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    from app.agents.state import get_agent_state as _get

    agent = get_agent(db, ws, agent_id, user)
    return {"runtime_state": _get(agent)}


@router.put("/workspaces/{workspace_id}/agents/{agent_id}/state")
def put_agent_state(agent_id: str, body: dict,
                    ws: Workspace = Depends(resolve_workspace),
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    from app.agents.state import update_agent_state

    agent = get_agent(db, ws, agent_id, user)
    state = update_agent_state(db, agent, (body or {}).get("runtime_state", body or {}))
    return {"runtime_state": state}


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/chat",
             response_model=schemas.ChatOut)
def post_chat(
    agent_id: str,
    body: schemas.ChatIn,
    request: Request,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    embed_fn=Depends(get_embed_fn),
    generate_fn=Depends(get_generate_fn),
):
    from app.common.rate_limit import check

    check("chat", str(user.id), calls=60, period_seconds=60)
    agent = get_agent(db, ws, agent_id, user)
    return service_chat(db, ws, agent, user, body.message, body.conversation_id,
                        body.kb_id, embed_fn=embed_fn, generate_fn=generate_fn)


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/chat/preview")
def post_chat_preview(
    agent_id: str,
    body: schemas.ChatPreviewIn,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    embed_fn=Depends(get_embed_fn),
    generate_fn=Depends(get_generate_fn),
):
    """Grounded one-off answer for source panels; never writes chat history."""
    from app.common.rate_limit import check
    from app.agents.service import chat_preview

    check("chat", str(user.id), calls=60, period_seconds=60)
    agent = get_agent(db, ws, agent_id, user)
    return chat_preview(
        db, ws, agent, user, body.message, body.kb_id,
        embed_fn=embed_fn, generate_fn=generate_fn,
    )


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/chat/stream")
def post_chat_stream(
    agent_id: str,
    body: schemas.ChatIn,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    embed_fn=Depends(get_embed_fn),
    stream_fn=Depends(get_stream_fn),
    generate_fn=Depends(get_generate_fn),
):
    from fastapi import HTTPException

    if not (body.message or "").strip():
        raise HTTPException(422, "message is empty")
    agent = get_agent(db, ws, agent_id, user)
    return sse_response(chat_stream_events(db, ws, agent, user, body.message,
                                           body.conversation_id, body.kb_id,
                                           embed_fn=embed_fn, stream_fn=stream_fn,
                                           generate_fn=generate_fn))


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/chat/resume")
def post_chat_resume(
    agent_id: str,
    body: schemas.ChatResumeIn,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    embed_fn=Depends(get_embed_fn),
    stream_fn=Depends(get_stream_fn),
):
    """Continue a paused client-tool turn (browser executed web/maps).

    Body carries validated-in-server tool results; the server stores
    leads, runs the chain remainder on browser failure, and streams the
    final answer (same SSE frames as /chat/stream).
    """
    from app.common.rate_limit import check

    check("chat", str(user.id), calls=60, period_seconds=60)
    agent = get_agent(db, ws, agent_id, user)
    return sse_response(chat_resume_events(
        db, ws, agent, user, body.conversation_id,
        [r.model_dump() for r in body.results],
        embed_fn=embed_fn, stream_fn=stream_fn))


@router.get("/workspaces/{workspace_id}/leads",
            response_model=list[schemas.LeadOut])
def get_leads(ws: Workspace = Depends(resolve_workspace),
              db: Session = Depends(get_db),
              user: User = Depends(get_current_user),
              status: str | None = None, limit: int = 200,
              conversation_id: str | None = None):
    from app.agents import leads as _leads

    return _leads.list_leads(db, ws.id, status=status, limit=limit,
                             conversation_id=conversation_id)


@router.patch("/workspaces/{workspace_id}/leads/status")
def patch_leads_status(body: schemas.LeadStatusIn,
                       ws: Workspace = Depends(resolve_workspace),
                       db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    from app.agents import leads as _leads

    return {"updated": _leads.set_status(db, ws.id, body.lead_ids, body.status)}


@router.get("/workspaces/{workspace_id}/leads/export")
def export_leads(ws: Workspace = Depends(resolve_workspace),
                 db: Session = Depends(get_db),
                 user: User = Depends(get_current_user),
                 format: str = "csv",
                 conversation_id: str | None = None):
    """Download leads materialized FROM the DB (csv default, xlsx opt)."""
    from fastapi.responses import Response

    from app.agents import leads as _leads

    rows = _leads.list_leads(db, ws.id, limit=1000,
                             conversation_id=conversation_id)
    fmt = (format or "csv").lower()
    if fmt == "xlsx":
        return Response(content=_leads.leads_to_xlsx(rows),
                        media_type="application/vnd.openxmlformats-officedocument"
                                   ".spreadsheetml.sheet",
                        headers={"Content-Disposition":
                                 'attachment; filename="leads.xlsx"'})
    return Response(content=_leads.leads_to_csv(rows),
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             'attachment; filename="leads.csv"'})


@router.post("/workspaces/{workspace_id}/knowledge-bases/{kb_id}/leads/import",
             status_code=201)
def import_leads_to_kb(kb_id: str, body: schemas.LeadsImportIn,
                       ws: Workspace = Depends(resolve_workspace),
                       db: Session = Depends(get_db),
                       user: User = Depends(get_current_user),
                       storage=Depends(get_storage)):
    """Save selected DB leads as a CSV source in a KB (normal ingest)."""
    from app.agents import leads as _leads
    from app.knowledge.schemas import SourceOut
    from app.knowledge.service import get_kb

    kb = get_kb(db, ws, kb_id)
    row = _leads.import_to_kb(db, ws, kb, user, body.lead_ids, storage)
    return SourceOut.model_validate(row).model_dump(mode="json")


@router.get("/workspaces/{workspace_id}/agents/{agent_id}/conversations",
            response_model=list[schemas.ConversationOut])
def get_conversations(agent_id: str,
                      ws: Workspace = Depends(resolve_workspace),
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    return list_conversations(db, ws, get_agent(db, ws, agent_id, user), user)


@router.patch("/workspaces/{workspace_id}/agents/{agent_id}/conversations/{conversation_id}",
              response_model=schemas.ConversationOut)
def patch_conversation_one(agent_id: str, conversation_id: str,
                           body: schemas.ConversationPatch,
                           ws: Workspace = Depends(resolve_workspace),
                           db: Session = Depends(get_db),
                           user: User = Depends(get_current_user)):
    return patch_conversation(db, ws, get_agent(db, ws, agent_id, user), user,
                              conversation_id, title=body.title, pinned=body.pinned)


@router.get("/workspaces/{workspace_id}/conversations/{conversation_id}/messages",
            response_model=list[schemas.MessageOut])
def get_conversation_messages(conversation_id: str,
                              ws: Workspace = Depends(resolve_workspace),
                              db: Session = Depends(get_db),
                              user: User = Depends(get_current_user)):
    return get_messages(db, ws, user, conversation_id)
