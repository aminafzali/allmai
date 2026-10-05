"""Trace/workspace/user propagation for usage attribution.

ContextVars (async-safe) carry the current turn's identity so deep model
calls (provider methods with no db/session in scope) still attribute to
the right workspace/user under one trace_id. Callers bind what they know;
missing pieces stay None and are stored as unattributed (never guessed).
"""

from __future__ import annotations

import contextvars
import uuid

_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "usage_trace_id", default=None)
_workspace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "usage_workspace_id", default=None)
_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "usage_user_id", default=None)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def bind_usage_context(workspace_id=None, user_id=None,
                       trace_id: str | None = None) -> str:
    """Bind (and return) the trace id for this turn/request.

    None arguments leave the current binding untouched, so layers bind
    only what they know (hybrid_search binds workspace; run_agent binds
    all three; the middleware binds a bare trace per request).
    """
    if trace_id is None:
        trace_id = _trace_id.get() or new_trace_id()
    _trace_id.set(str(trace_id))
    if workspace_id is not None:
        _workspace_id.set(str(workspace_id))
    if user_id is not None:
        _user_id.set(str(user_id))
    return str(trace_id)


def current_trace_id() -> str | None:
    return _trace_id.get()


def current_workspace_id() -> str | None:
    return _workspace_id.get()


def current_user_id() -> str | None:
    return _user_id.get()


def clear_usage_context() -> None:
    _trace_id.set(None)
    _workspace_id.set(None)
    _user_id.set(None)


class UsageTraceMiddleware:
    """Pure-ASGI middleware: one trace_id per request.

    No DB, no auth inspection — it only seeds the trace so every span
    recorded during the request links together. Workspace/user binding
    happens deeper, where the identity is actually known.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            bind_usage_context(trace_id=new_trace_id())
        await self.app(scope, receive, send)
