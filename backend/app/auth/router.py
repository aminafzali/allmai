from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth import schemas
from app.auth.service import (
    audit,
    authenticate,
    get_current_user,
    issue_pair,
    refresh_pair,
    register_user,
    revoke_token,
)
from app.core.database import get_db
from app.users.models import User
from app.users.schemas import UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: schemas.RegisterIn, db: Session = Depends(get_db)):
    return register_user(db, body.email, body.password, body.full_name)


@router.post("/login", response_model=schemas.TokenOut)
def login(body: schemas.LoginIn, request: Request, db: Session = Depends(get_db)):
    from app.common.rate_limit import check

    ip = _ip(request)
    check("login", f"{ip}:{body.email.strip().lower()}", calls=10, period_seconds=60)
    user = authenticate(db, body.email, body.password)
    if not user:
        audit(db, "auth.login_failed", entity="user", meta={"email": body.email}, ip=_ip(request))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    audit(db, "auth.login", actor_user_id=user.id, entity="user", entity_id=user.id, ip=_ip(request))
    return issue_pair(db, user)


@router.post("/refresh", response_model=schemas.TokenOut)
def refresh(body: schemas.RefreshIn, request: Request, db: Session = Depends(get_db)):
    from app.common.rate_limit import check

    check("refresh", _ip(request) or "unknown", calls=30, period_seconds=60)
    return refresh_pair(db, body.refresh_token)


@router.post("/logout")
def logout(body: schemas.RefreshIn, db: Session = Depends(get_db)):
    revoke_token(db, body.refresh_token)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
