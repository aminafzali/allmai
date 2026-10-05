"""Single fail-safe entry point for usage events.

record_event() NEVER raises and NEVER touches the caller's transaction:
it opens its own short session, binds the right RLS context per row
(workspace rows under that workspace, system rows under admin), inserts
one row and closes. Under pytest (or any DB outage) the event is dropped
with a warning instead of breaking the product path.

Tests capture payloads by monkeypatching _emit.
"""

from __future__ import annotations

import logging
import os

from app.common.base import coerce_uuid
from app.usage import context as _ctx
from app.usage.models import SPANS
from app.usage.pricing import estimate_cost_usd

logger = logging.getLogger(__name__)

_emit = None  # replaced by _default_emit below; tests monkeypatch it


def _coerce_id(value):
    if value is None or value == "":
        return None
    try:
        return coerce_uuid(value)
    except Exception:
        return None


def _default_emit(payload: dict) -> None:
    """Persist one event on an isolated session (never the caller's)."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        # Hermetic suite: no live PG traffic from unit tests. Tests that
        # assert recording monkeypatch _emit instead.
        return
    from app.core.database import SessionLocal

    import app.models  # noqa: F401 (full metadata: every FK target
    # must be mapped in processes that never imported the models,
    # e.g. eval scripts — otherwise UsageEvent cannot configure)

    db = SessionLocal()
    try:
        from app.usage.models import UsageEvent

        ws = payload.get("workspace_id")
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            from sqlalchemy import text as _text

            if ws is not None:
                db.execute(_text("SELECT set_config('app.workspace_id', :v, false)"),
                           {"v": str(ws)})
                db.execute(_text("SELECT set_config('app.is_admin', '', false)"))
            else:
                # System/global rows are admin-only by policy.
                db.execute(_text("SELECT set_config('app.is_admin', 'true', false)"))
        db.add(UsageEvent(**payload))
        db.commit()
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning("usage event dropped (%s: %s)",
                       type(exc).__name__, str(exc)[:160])
    finally:
        try:
            from app.core.database import reset_workspace_context

            reset_workspace_context(db)
        except Exception:
            pass
        try:
            db.close()
        except Exception:
            pass


_emit = _default_emit


def record_event(span: str, provider: str | None = None,
                 model: str | None = None,
                 prompt_tokens: int = 0, completion_tokens: int = 0,
                 calls: int = 1, input_chars: int = 0,
                 latency_ms: float | None = None,
                 ok: bool = True, error: str = "",
                 workspace_id=None, user_id=None, trace_id: str | None = None,
                 meta: dict | None = None) -> None:
    """Build the event payload (context fills gaps) and emit it."""
    if span not in SPANS:
        logger.warning("unknown usage span %r dropped", span)
        return
    try:
        prompt_tokens = max(0, int(prompt_tokens or 0))
    except (TypeError, ValueError):
        prompt_tokens = 0
    try:
        completion_tokens = max(0, int(completion_tokens or 0))
    except (TypeError, ValueError):
        completion_tokens = 0
    try:
        calls = max(1, int(calls or 1))
    except (TypeError, ValueError):
        calls = 1
    try:
        input_chars = max(0, int(input_chars or 0))
    except (TypeError, ValueError):
        input_chars = 0
    payload = {
        "workspace_id": _coerce_id(workspace_id if workspace_id is not None
                                   else _ctx.current_workspace_id()),
        "user_id": _coerce_id(user_id if user_id is not None
                              else _ctx.current_user_id()),
        "trace_id": str(trace_id or _ctx.current_trace_id() or ""),
        "span": span,
        "provider": str(provider or "")[:32],
        "model": str(model or "")[:128],
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "calls": calls,
        "input_chars": input_chars,
        "latency_ms": (float(latency_ms) if latency_ms is not None else None),
        "ok": bool(ok),
        "error": str(error or "")[:500],
        "cost_usd": estimate_cost_usd(provider, model,
                                      prompt_tokens, completion_tokens),
        "meta": dict(meta or {}),
    }
    try:
        _emit(payload)
    except Exception as exc:
        logger.warning("usage emit failed (%s)", type(exc).__name__)
