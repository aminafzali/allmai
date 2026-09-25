"""Workspace (tenant) guards for the application layer.

Every service function that touches tenant-aware data MUST accept an
explicit ``workspace_id`` and call ``require_workspace_id`` first.
This makes a missing tenant context a loud error instead of a silent
cross-workspace leak. Database RLS is the second line of defence.
"""

from uuid import UUID


class MissingWorkspaceContextError(ValueError):
    """Raised when tenant-aware code runs without a workspace context."""


def require_workspace_id(workspace_id: UUID | str | None) -> str:
    """Validate presence of a workspace context; return it as string."""
    if workspace_id is None:
        raise MissingWorkspaceContextError(
            "workspace_id is required: no query/retrieval/file/memory/agent "
            "access is allowed without a workspace context."
        )
    text = str(workspace_id).strip()
    if not text:
        raise MissingWorkspaceContextError("workspace_id must not be empty.")
    return text


def _coerce_key(value: str):
    """Return a UUID object when parseable (portable across PG/SQLite)."""
    try:
        return UUID(value)
    except (ValueError, AttributeError, TypeError):
        return value


def scoped_filter(model, workspace_id: UUID | str | None):
    """Build a ``model.workspace_id == ...`` filter, guarding the context.

    Usage: ``db.query(KnowledgeBase).filter(scoped_filter(KnowledgeBase, wid))``.
    """
    wid = require_workspace_id(workspace_id)
    return model.workspace_id == _coerce_key(wid)
