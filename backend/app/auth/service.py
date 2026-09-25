"""Auth service: register / login / refresh rotation / logout / RBAC / audit."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.auth.models import RefreshToken
from app.common.audit import AuditLog
from app.common.base import coerce_uuid
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password
from app.users.models import User

bearer_scheme = HTTPBearer(auto_error=False)
settings = get_settings()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(expires_at: datetime) -> bool:
    # SQLite returns naive datetimes; treat them as UTC.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < utcnow()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def audit(
    db: Session,
    action: str,
    actor_user_id=None,
    workspace_id=None,
    entity: str = "",
    entity_id: str = "",
    meta: dict | None = None,
    ip: str = "",
) -> None:
    from app.common.pii import redact

    clean_meta = {
        k: (redact(v) if isinstance(v, str) else v) for k, v in (meta or {}).items()
    }
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action=action,
            entity=entity,
            entity_id=str(entity_id or ""),
            meta=clean_meta,
            ip=ip,
        )
    )
    db.commit()


def register_user(db: Session, email: str, password: str, full_name: str = "") -> User:
    email = email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    user = User(email=email, password_hash=hash_password(password), full_name=full_name)
    db.add(user)
    db.commit()
    db.refresh(user)
    audit(db, "auth.register", actor_user_id=user.id, entity="user", entity_id=user.id)
    return user


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if user and verify_password(password, user.password_hash):
        return user
    return None


def _new_refresh_token(db: Session, user: User) -> str:
    raw = secrets.token_urlsafe(48)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=_hash_token(raw),
            expires_at=utcnow() + timedelta(days=settings.REFRESH_TOKEN_DAYS),
        )
    )
    db.commit()
    return raw


def issue_pair(db: Session, user: User) -> dict:
    return {
        "access_token": create_access_token(str(user.id)),
        "refresh_token": _new_refresh_token(db, user),
        "token_type": "bearer",
    }


def refresh_pair(db: Session, refresh_token: str) -> dict:
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == _hash_token(refresh_token)).first()
    if not row or row.revoked or _is_expired(row.expires_at):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh token")
    user = db.query(User).filter(User.id == row.user_id).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh token")
    row.revoked = True  # rotation: the old token dies here
    db.commit()
    pair = issue_pair(db, user)
    audit(db, "auth.refresh", actor_user_id=user.id, entity="user", entity_id=user.id)
    return pair


def revoke_token(db: Session, refresh_token: str) -> None:
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == _hash_token(refresh_token)).first()
    if row and not row.revoked:
        row.revoked = True
        db.commit()
        audit(db, "auth.logout", actor_user_id=row.user_id, entity="user", entity_id=row.user_id)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing credentials")
    try:
        claims = decode_access_token(creds.credentials)
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
    user = db.query(User).filter(User.id == coerce_uuid(claims["sub"])).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown user")
    from app.core.database import bind_auth_context

    bind_auth_context(db, user)
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return user
