from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.admin import schemas
from app.ai.settings import get_setting, list_settings, upsert_setting
from app.auth.service import audit, require_admin
from app.common.audit import AuditLog
from app.core.database import get_db
from app.users.models import User

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/ai-settings")
def read_ai_settings(
    db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    return list_settings(db)


@router.put("/ai-settings")
def update_ai_settings(
    body: schemas.AISettingUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        value = upsert_setting(db, body.key, body.value)
    except KeyError:
        raise HTTPException(422, f"unknown AI setting: {body.key!r}")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    audit(
        db, "admin.ai_settings.update", actor_user_id=admin.id,
        entity="ai_setting", entity_id=body.key, meta={"value": value},
    )
    return {body.key: get_setting(db, body.key)}


@router.get("/audit-logs")
def read_audit_logs(    limit: int = 50, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    limit = max(1, min(limit, 200))
    rows = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
    return [
        {
            "id": str(r.id),
            "action": r.action,
            "actor": str(r.actor_user_id) if r.actor_user_id else None,
            "entity": r.entity,
            "entity_id": r.entity_id,
            "ip": r.ip,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.get("/users", response_model=list[schemas.AdminUserOut])
def read_users(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    from app.users.models import User as _User

    rows = db.query(_User).order_by(_User.created_at.desc()).limit(500).all()
    return [
        schemas.AdminUserOut(
            id=str(r.id), email=r.email, full_name=r.full_name,
            is_admin=r.is_admin, created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


@router.patch("/users/{user_id}", response_model=schemas.AdminUserOut)
def update_user(
    user_id: str,
    body: schemas.AdminUserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    from fastapi import HTTPException as _HTTPException

    from app.common.base import coerce_uuid as _coerce
    from app.users.models import User as _User

    target = db.query(_User).filter(_User.id == _coerce(user_id)).first()
    if target is None:
        raise _HTTPException(404, "user not found")
    target.is_admin = bool(body.is_admin)
    db.commit()
    audit(db, "admin.user_update", actor_user_id=admin.id,
          entity="user", entity_id=target.id, meta={"is_admin": target.is_admin})
    return schemas.AdminUserOut(
        id=str(target.id), email=target.email, full_name=target.full_name,
        is_admin=target.is_admin, created_at=target.created_at.isoformat(),
    )
