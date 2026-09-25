"""Admin-only Agent Definition endpoints (Phase 1).

Definitions + global-KB assignment are managed ONLY here. Instances
never mutate global assignment; they read it as inherited read-only.
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.agents import schemas
from app.agents.definitions_service import (
    assign_knowledge,
    assigned_global_kb_ids,
    create_definition,
    delete_definition,
    get_definition,
    list_definitions,
    update_definition,
)
from app.auth.service import audit, require_admin
from app.core.database import get_db
from app.storage.s3 import get_storage
from app.users.models import User

router = APIRouter(prefix="/admin/agent-definitions", tags=["admin"])


@router.get("", response_model=list[schemas.DefinitionOut])
def list_defs(db: Session = Depends(get_db),
              admin: User = Depends(require_admin)):
    return list_definitions(db)


@router.post("", response_model=schemas.DefinitionOut, status_code=201)
def create_def(body: schemas.DefinitionCreate,
               db: Session = Depends(get_db),
               admin: User = Depends(require_admin)):
    row = create_definition(db, body.key, body.title, body.type,
                            description=body.description,
                            instructions=body.instructions,
                            behavior_rules=body.behavior_rules,
                            methodology=body.methodology,
                            capabilities=body.capabilities,
                            tools=body.tools, workflow=body.workflow,
                            model_defaults=body.model_defaults,
                            safety_rules=body.safety_rules,
                            output_format=body.output_format,
                            is_active=body.is_active)
    audit(db, "admin.definition_create", actor_user_id=admin.id,
          entity="agent_definition", entity_id=row.id, meta={"key": row.key})
    return row


@router.get("/{definition_id}", response_model=schemas.DefinitionOut)
def get_def(definition_id: str, db: Session = Depends(get_db),
            admin: User = Depends(require_admin)):
    return get_definition(db, definition_id)


@router.put("/{definition_id}", response_model=schemas.DefinitionOut)
def put_def(definition_id: str, body: schemas.DefinitionUpdate,
            db: Session = Depends(get_db),
            admin: User = Depends(require_admin)):
    row = update_definition(db, definition_id, body.model_dump(exclude_unset=True))
    audit(db, "admin.definition_update", actor_user_id=admin.id,
          entity="agent_definition", entity_id=row.id)
    return row


@router.delete("/{definition_id}", status_code=204)
def del_def(definition_id: str, db: Session = Depends(get_db),
            admin: User = Depends(require_admin)):
    delete_definition(db, definition_id)
    audit(db, "admin.definition_delete", actor_user_id=admin.id,
          entity="agent_definition", entity_id=definition_id)
    return None


@router.get("/{definition_id}/knowledge")
def get_def_knowledge(definition_id: str, db: Session = Depends(get_db),
                      admin: User = Depends(require_admin)):
    get_definition(db, definition_id)  # 404 if missing
    return {"kb_ids": [str(k) for k in assigned_global_kb_ids(db, definition_id)]}


@router.put("/{definition_id}/knowledge")
def put_def_knowledge(definition_id: str, body: schemas.DefinitionKnowledgeUpdate,
                      db: Session = Depends(get_db),
                      admin: User = Depends(require_admin)):
    # Locked: only global KBs accepted; workspace KB -> 422 from service.
    kids = assign_knowledge(db, definition_id, body.kb_ids)
    audit(db, "admin.definition_knowledge", actor_user_id=admin.id,
          entity="agent_definition", entity_id=definition_id,
          meta={"kb_ids": [str(k) for k in kids]})
    return {"kb_ids": [str(k) for k in kids]}


@router.get("/{definition_id}/instances")
def get_def_instances(definition_id: str, db: Session = Depends(get_db),
                      admin: User = Depends(require_admin)):
    from app.agents.models import Agent

    get_definition(db, definition_id)  # 404 if missing
    from app.common.base import coerce_uuid

    rows = (
        db.query(Agent)
        .filter(Agent.definition_id == coerce_uuid(definition_id))
        .order_by(Agent.created_at.desc()).all()
    )
    return [{"id": str(r.id), "workspace_id": str(r.workspace_id),
             "key": r.key, "name": r.name,
             "owner_user_id": str(r.owner_user_id) if r.owner_user_id else None,
             "created_at": r.created_at.isoformat()} for r in rows]


# ---------- Global Knowledge (admin-managed; assignment lives on Definitions) ----------

global_kb_router = APIRouter(prefix="/admin/global-knowledge-bases", tags=["admin"])


@global_kb_router.get("")
def list_global_kbs(db: Session = Depends(get_db),
                    admin: User = Depends(require_admin)):
    from app.knowledge.schemas import KnowledgeBaseOut
    from app.knowledge.service import list_global_kbs as _list

    return [KnowledgeBaseOut.model_validate(r) for r in _list(db)]


@global_kb_router.post("", status_code=201)
def create_global_kb(body: dict,
                     db: Session = Depends(get_db),
                     admin: User = Depends(require_admin)):
    from app.knowledge.schemas import KnowledgeBaseOut
    from app.knowledge.service import create_global_kb as _create

    row = _create(db, admin, str(body.get("title", "")),
                  str(body.get("description", "")))
    audit(db, "admin.global_kb_create", actor_user_id=admin.id,
          entity="knowledge_base", entity_id=row.id)
    return KnowledgeBaseOut.model_validate(row).model_dump(mode="json")


@global_kb_router.post("/{kb_id}/sources", status_code=201)
def create_global_source(kb_id: str,
                         type: str = Form(...),
                         file: UploadFile = File(...),
                         db: Session = Depends(get_db),
                         admin: User = Depends(require_admin),
                         storage=Depends(get_storage)):
    """Admin-only file intake into a GLOBAL KB (Phase 2).

    The worker ingests it under a transient admin context; chat retrieval
    stays gated by definition assignment (Phase 1 RLS, unchanged).
    """
    from app.common.base import coerce_uuid
    from app.knowledge.schemas import SourceOut
    from app.knowledge.service import create_global_file_source
    from app.knowledge.models import KnowledgeBase

    kb = (
        db.query(KnowledgeBase)
        .filter(KnowledgeBase.id == coerce_uuid(kb_id),
                KnowledgeBase.scope == "global")
        .first()
    )
    if kb is None:
        raise HTTPException(404, "global knowledge base not found")
    blob = file.file.read()
    row = create_global_file_source(db, kb, admin, type,
                                    file.filename or "file", blob, storage)
    return SourceOut.model_validate(row).model_dump(mode="json")


@global_kb_router.get("/{kb_id}/sources")
def list_global_sources(kb_id: str, db: Session = Depends(get_db),
                        admin: User = Depends(require_admin)):
    from app.common.base import coerce_uuid
    from app.knowledge.models import KnowledgeBase as _KB
    from app.knowledge.models import Source as _Source
    from app.knowledge.schemas import SourceOut as _SourceOut

    kb = db.query(_KB).filter(_KB.id == coerce_uuid(kb_id),
                              _KB.scope == "global").first()
    if kb is None:
        raise HTTPException(404, "global knowledge base not found")
    rows = (db.query(_Source).filter(_Source.kb_id == kb.id)
            .order_by(_Source.created_at.desc()).all())
    return [_SourceOut.model_validate(r).model_dump(mode="json") for r in rows]


@global_kb_router.get("/{kb_id}/sources/{source_id}/processing")
def get_global_source_processing(kb_id: str, source_id: str,
                                 db: Session = Depends(get_db),
                                 admin: User = Depends(require_admin)):
    """Same Document Core detail view as the workspace endpoint, admin-gated."""
    from app.common.base import coerce_uuid
    from app.knowledge.models import KnowledgeBase as _KB
    from app.knowledge.models import Source as _Source
    from app.knowledge.service import source_processing_detail

    kb = db.query(_KB).filter(_KB.id == coerce_uuid(kb_id),
                              _KB.scope == "global").first()
    if kb is None:
        raise HTTPException(404, "global knowledge base not found")
    src = db.query(_Source).filter(
        _Source.id == coerce_uuid(source_id), _Source.kb_id == kb.id).first()
    if src is None:
        raise HTTPException(404, "source not found")
    return source_processing_detail(db, src)
