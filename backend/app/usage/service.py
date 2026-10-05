"""Usage aggregation for the read surface (billing math comes later).

summarize_usage() is the single query behind both endpoints: totals +
per-(span, provider, model) breakdown + recent rows, all RLS-gated by
the caller's session (members see their workspace, admins see all).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.common.base import coerce_uuid
from app.usage.models import UsageEvent


def _parse_since(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def summarize_usage(db: Session, workspace_id=None, user_id=None,
                    since: str | None = None, span: str | None = None,
                    limit_rows: int = 100) -> dict:
    """Aggregate usage events. Filters are ANDed; unknown span -> 422-ish
    empty result is the caller's job — here it just matches nothing."""
    q = db.query(UsageEvent)
    if workspace_id is not None:
        q = q.filter(UsageEvent.workspace_id == coerce_uuid(workspace_id))
    if user_id is not None:
        q = q.filter(UsageEvent.user_id == coerce_uuid(user_id))
    ts = _parse_since(since)
    if ts is not None:
        q = q.filter(UsageEvent.created_at >= ts)
    if span:
        q = q.filter(UsageEvent.span == str(span))

    totals = q.with_entities(
        func.count(UsageEvent.id),
        func.coalesce(func.sum(UsageEvent.calls), 0),
        func.coalesce(func.sum(UsageEvent.prompt_tokens), 0),
        func.coalesce(func.sum(UsageEvent.completion_tokens), 0),
        func.coalesce(func.sum(UsageEvent.total_tokens), 0),
        func.coalesce(func.sum(UsageEvent.cost_usd), 0),
        func.count(UsageEvent.id).filter(UsageEvent.ok.is_(False)),
        func.avg(UsageEvent.latency_ms),
    ).one()
    by_model = q.with_entities(
        UsageEvent.span, UsageEvent.provider, UsageEvent.model,
        func.count(UsageEvent.id),
        func.coalesce(func.sum(UsageEvent.calls), 0),
        func.coalesce(func.sum(UsageEvent.total_tokens), 0),
        func.coalesce(func.sum(UsageEvent.cost_usd), 0),
    ).group_by(UsageEvent.span, UsageEvent.provider,
               UsageEvent.model).all()
    rows = (q.order_by(UsageEvent.created_at.desc())
            .limit(max(0, min(int(limit_rows or 0), 200))).all())
    return {
        "totals": {
            "events": int(totals[0] or 0),
            "calls": int(totals[1] or 0),
            "prompt_tokens": int(totals[2] or 0),
            "completion_tokens": int(totals[3] or 0),
            "total_tokens": int(totals[4] or 0),
            "cost_usd": float(totals[5] or 0),
            "errors": int(totals[6] or 0),
            "avg_latency_ms": round(float(totals[7]), 1) if totals[7] is not None else None,
        },
        "by_model": [
            {"span": r[0], "provider": r[1], "model": r[2],
             "events": int(r[3] or 0), "calls": int(r[4] or 0),
             "total_tokens": int(r[5] or 0),
             "cost_usd": float(r[6] or 0)}
            for r in by_model
        ],
        "rows": [
            {"id": str(r.id), "trace_id": r.trace_id, "span": r.span,
             "provider": r.provider, "model": r.model,
             "prompt_tokens": r.prompt_tokens,
             "completion_tokens": r.completion_tokens,
             "calls": r.calls, "input_chars": r.input_chars,
             "latency_ms": r.latency_ms, "ok": r.ok, "error": r.error,
             "cost_usd": float(r.cost_usd) if r.cost_usd is not None else None,
             "workspace_id": str(r.workspace_id) if r.workspace_id else None,
             "user_id": str(r.user_id) if r.user_id else None,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows
        ],
    }
