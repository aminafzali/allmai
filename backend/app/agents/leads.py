"""Lead persistence + export. Leads are first-class tenant rows
(``leads`` table, migration 0012, workspace RLS); export and KB-import
materialize FROM the DB, never from transient chat state.
"""

from __future__ import annotations

import csv
import io

from sqlalchemy.orm import Session

from app.agents.lead_models import LEAD_STATUSES, Lead
from app.common.base import coerce_uuid

CSV_COLUMNS = ["name", "address", "phone", "hours", "website",
               "lat", "lng", "query", "source", "status", "created_at"]


def save_leads(db: Session, workspace_id, agent_id, conversation_id,
               query: str, items: list[dict], source: str) -> list[Lead]:
    """Insert validated place dicts (see places_providers.validate_*).

    De-duplicates on (workspace, name, address) so repeated searches
    ("ادامه بده") accumulate instead of duplicating. Returns only the
    NEWLY inserted rows.
    """
    existing = {( (n or "").strip(), (a or "").strip()) for n, a in
                db.query(Lead.name, Lead.address)
                .filter(Lead.workspace_id == coerce_uuid(workspace_id)).all()}
    rows = []
    for it in items or []:
        if not isinstance(it, dict) or not (it.get("name") or "").strip():
            continue
        key = (it.get("name", "").strip(), (it.get("address") or "").strip())
        if key in existing:
            continue
        existing.add(key)
        raw = it.get("raw") if isinstance(it.get("raw"), dict) else None
        if raw is None:
            raw = {k: v for k, v in it.items()
                   if k not in ("name", "address", "phone", "hours",
                                "website", "lat", "lng", "maps_uri", "rating")}
        rows.append(Lead(
            workspace_id=coerce_uuid(workspace_id),
            agent_id=coerce_uuid(agent_id) if agent_id else None,
            conversation_id=coerce_uuid(conversation_id) if conversation_id else None,
            query=(query or "")[:500],
            name=it.get("name", "")[:300],
            address=it.get("address", "")[:1000],
            phone=it.get("phone", "")[:100],
            hours=it.get("hours", "")[:300],
            website=it.get("website", "")[:512],
            lat=it.get("lat"), lng=it.get("lng"),
            source=(source or "")[:32],
            raw=dict(raw),
            status="new",
        ))
    if rows:
        db.add_all(rows)
        db.commit()
        for r in rows:
            db.refresh(r)
    return rows


def list_leads(db: Session, workspace_id, status: str | None = None,
               limit: int = 200, conversation_id=None) -> list[Lead]:
    q = (db.query(Lead)
         .filter(Lead.workspace_id == coerce_uuid(workspace_id))
         .order_by(Lead.created_at.desc()))
    if status:
        q = q.filter(Lead.status == status)
    if conversation_id:
        q = q.filter(Lead.conversation_id == coerce_uuid(conversation_id))
    return q.limit(max(1, min(1000, limit or 200))).all()


def lead_to_row(lead: Lead) -> list:
    return [lead.name or "", lead.address or "", lead.phone or "",
            lead.hours or "", lead.website or "",
            "" if lead.lat is None else lead.lat,
            "" if lead.lng is None else lead.lng,
            lead.query or "", lead.source or "", lead.status or "",
            lead.created_at.isoformat() if lead.created_at else ""]


def leads_to_csv(leads: list[Lead]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "address", "phone", "hours", "website",
                "lat", "lng", "query", "source", "status", "created_at"])
    for lead in leads:
        w.writerow(lead_to_row(lead))
    return "\ufeff".encode("utf-8") + buf.getvalue().encode("utf-8")  # BOM for Excel


def leads_to_xlsx(leads: list[Lead]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "leads"
    header = ["name", "address", "phone", "hours", "website",
              "lat", "lng", "query", "source", "status", "created_at"]
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True)
    for lead in leads:
        ws.append(lead_to_row(lead))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def set_status(db: Session, workspace_id, lead_ids: list, status: str) -> int:
    if status not in LEAD_STATUSES:
        from fastapi import HTTPException
        raise HTTPException(422, f"status must be one of {LEAD_STATUSES}")
    ids = [coerce_uuid(i) for i in (lead_ids or [])]
    if not ids:
        return 0
    n = (db.query(Lead)
         .filter(Lead.workspace_id == coerce_uuid(workspace_id),
                 Lead.id.in_(ids))
         .update({"status": status}, synchronize_session=False))
    db.commit()
    return n


def import_to_kb(db: Session, ws, kb, user, lead_ids: list, storage):
    """Save selected leads as a CSV source in a KB (normal ingest)."""
    from app.knowledge.service import create_file_source

    ids = [coerce_uuid(i) for i in (lead_ids or [])]
    leads = (db.query(Lead)
             .filter(Lead.workspace_id == ws.id, Lead.id.in_(ids))
             .order_by(Lead.created_at.desc()).all()) if ids else []
    if not leads:
        from fastapi import HTTPException
        raise HTTPException(404, "no matching leads")
    blob = leads_to_csv(leads)
    return create_file_source(db, ws, kb, user, "csv",
                              f"leads-{len(leads)}.csv", blob, storage)
