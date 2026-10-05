"""Usage read surface: admin overview + workspace summary.

Measurement only — no quotas, no charges, no subscription logic.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.service import get_current_user, require_admin
from app.core.database import get_db
from app.usage.service import summarize_usage
from app.users.models import User
from app.workspaces.service import require_workspace_role, resolve_workspace

router = APIRouter(tags=["usage"])


@router.get("/admin/usage/summary")
def admin_usage_summary(workspace_id: str | None = None,
                        user_id: str | None = None,
                        since: str | None = None,
                        span: str | None = None,
                        limit_rows: int = 100,
                        db: Session = Depends(get_db),
                        admin: User = Depends(require_admin)):
    """Cross-workspace usage (admin only, includes unattributed rows)."""
    return summarize_usage(db, workspace_id=workspace_id, user_id=user_id,
                           since=since, span=span, limit_rows=limit_rows)


@router.get("/workspaces/{workspace_id}/usage/summary")
def workspace_usage_summary(since: str | None = None,
                            span: str | None = None,
                            limit_rows: int = 100,
                            ws=Depends(resolve_workspace),
                            db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    """This workspace's own usage. Owner/admin role required (same gate as
    the retrieval debugger); members cannot see costs."""
    require_workspace_role(db, ws, user, ("owner", "admin"))
    return summarize_usage(db, workspace_id=ws.id, since=since, span=span,
                           limit_rows=limit_rows)
