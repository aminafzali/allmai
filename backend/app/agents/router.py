from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.agents import schemas
from app.agents.service import (
    chat as service_chat,
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


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/chat/stream")
def post_chat_stream(
    agent_id: str,
    body: schemas.ChatIn,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    embed_fn=Depends(get_embed_fn),
    stream_fn=Depends(get_stream_fn),
):
    from fastapi import HTTPException

    if not (body.message or "").strip():
        raise HTTPException(422, "message is empty")
    agent = get_agent(db, ws, agent_id, user)
    return sse_response(chat_stream_events(db, ws, agent, user, body.message,
                                           body.conversation_id, body.kb_id,
                                           embed_fn=embed_fn, stream_fn=stream_fn))


@router.get("/workspaces/{workspace_id}/agents/{agent_id}/conversations",
            response_model=list[schemas.ConversationOut])
def get_conversations(agent_id: str,
                      ws: Workspace = Depends(resolve_workspace),
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    return list_conversations(db, ws, get_agent(db, ws, agent_id, user), user)


@router.get("/workspaces/{workspace_id}/conversations/{conversation_id}/messages",
            response_model=list[schemas.MessageOut])
def get_conversation_messages(conversation_id: str,
                              ws: Workspace = Depends(resolve_workspace),
                              db: Session = Depends(get_db),
                              user: User = Depends(get_current_user)):
    return get_messages(db, ws, user, conversation_id)
