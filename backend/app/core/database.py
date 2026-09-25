"""Database engine, sessions and workspace (tenant) context.

Tenant isolation is enforced on two layers:
1. Application layer: every tenant-aware query must carry ``workspace_id``
   (see ``app.core.workspace``).
2. Database layer: PostgreSQL Row Level Security policies compare
   ``workspace_id`` against ``current_setting('app.workspace_id')``.

The context is bound at SESSION level (not per-transaction) so it survives
the commits services perform mid-request; ``get_db`` always resets it
before the connection returns to the pool, so no context can leak between
requests. No-op on non-Postgres databases (unit tests).
"""

from collections.abc import Iterator
from uuid import UUID

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.common.base import Base  # noqa: F401  (registry for Alembic)
from app.core.config import get_settings

settings = get_settings()

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                            expire_on_commit=False)

TENANT_INFO_KEY = "app.workspace_id"
TENANT_DEF_KEY = "app.definition_id"


def _is_postgres(db: Session) -> bool:
    bind = db.bind
    return bind is not None and bind.dialect.name == "postgresql"


def _sanitized(value: str) -> str:
    value = str(value or "")
    if value and not all(c in "0123456789abcdef-" for c in value.lower()):
        raise ValueError("invalid workspace context")
    return value


@event.listens_for(Session, "after_begin")
def _auto_bind_tenant(session: Session, transaction, connection) -> None:
    """Re-apply SET LOCAL on EVERY new transaction from session.info.

    Binds BOTH app.workspace_id AND app.definition_id so mid-request
    commits cannot drop either context. Values only come from our own
    bind calls below (validated hex/empty).
    """
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return
    wid = _sanitized(session.info.get(TENANT_INFO_KEY) or "")
    did = _sanitized(session.info.get(TENANT_DEF_KEY) or "")
    connection.exec_driver_sql(
        f"SELECT set_config('app.workspace_id', '{wid}', true)")
    connection.exec_driver_sql(
        f"SELECT set_config('app.definition_id', '{did}', true)")


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        reset_workspace_context(db)
        db.close()


def set_workspace_context(db: Session, workspace_id: UUID | str | None) -> None:
    """Bind this session to a workspace for RLS policies (until reset).

    Stored on session.info and applied via SET LOCAL to the current AND
    every future transaction of this session (see _auto_bind_tenant).
    Passing ``None`` clears the context.
    """
    if not _is_postgres(db):
        return
    value = "" if workspace_id is None else _sanitized(str(workspace_id))
    db.info[TENANT_INFO_KEY] = value
    db.execute(text("SELECT set_config('app.workspace_id', :v, true)"), {"v": value})


def set_definition_context(db: Session, definition_id: UUID | str | None) -> None:
    """Bind this session to an agent definition for global-KB RLS.

    Same transaction-safety contract as set_workspace_context: stored on
    session.info and re-applied on every new transaction via
    _auto_bind_tenant. Passing ``None`` clears the context.
    Only meaningful during an agent chat turn; callers must not persist
    it beyond the turn (reset_workspace_context clears it).
    """
    if not _is_postgres(db):
        return
    value = "" if definition_id is None else _sanitized(str(definition_id))
    db.info[TENANT_DEF_KEY] = value
    db.execute(text("SELECT set_config('app.definition_id', :v, true)"), {"v": value})


def bind_auth_context(db: Session, user) -> None:
    """Bind the authenticated identity (user id + admin flag) for RLS.

    Called by get_current_user so membership-based policies work on every
    authenticated request. user may be None (clears).
    """
    if not _is_postgres(db):
        return
    uid = "" if user is None else str(user.id)
    admin = "true" if user is not None and bool(user.is_admin) else ""
    db.execute(text("SELECT set_config('app.user_id', :v, false)"), {"v": uid})
    db.execute(text("SELECT set_config('app.is_admin', :v, false)"), {"v": admin})


def reset_workspace_context(db: Session) -> None:
    """Clear the session context. Called by get_db before pool release."""
    if not _is_postgres(db):
        return
    try:
        db.info.pop(TENANT_INFO_KEY, None)
        db.info.pop(TENANT_DEF_KEY, None)
        db.execute(text("SELECT set_config('app.workspace_id', '', true)"))
        db.execute(text("SELECT set_config('app.definition_id', '', true)"))
        db.execute(text("SELECT set_config('app.user_id', '', false)"))
        db.execute(text("SELECT set_config('app.is_admin', '', false)"))
        db.commit()
    except Exception:
        db.rollback()
