"""KB file tools: deterministic inventory + full-text file reads.

Unlike semantic retrieval (which guesses from top chunks), these answer
structurally: the COMPLETE file list of the agent's KBs, and the FULL
extracted text of a NAMED file handed to the LLM (translate / summarize /
quote requests). Used by node_retrieve_knowledge whenever the
knowledge tools are enabled — no extra LLM round, fully testable.
"""

from __future__ import annotations

from app.common.base import coerce_uuid
from app.knowledge.normalize import fa_tokens

FILE_TEXT_MAX_CHARS = 30000

_LIST_HINTS = (
    # fa — list/inventory questions
    "چه فایل", "چه فایلهای", "چه فایل‌هایی", "لیست فایل", "فهرست فایل",
    "فایلهای من", "فایل های من", "چه چیزهایی", "چی داری",
    "موجودی", "فهرست",
    # en
    "list files", "what files", "show files", "which files", "list all",
    "name the files",
)

# Bare name-asking hints: only count when a file-ish word is nearby
# ("اسماشون بگو" alone could mean anything; "... فایل ... اسماشون" is clear).
_LIST_WEAK_HINTS = (
    "اسماشون", "اسماشو", "اسامیشون", "نام ببر", "نام ببرید",
    "کدوم فایل", "کدام فایل", "فایل چی",
)
_LIST_CONTEXT_WORDS = (
    "فایل", "سند", "مدرک", "بیس", "پایگاه", "دانش", "file",
)

VERBATIM_HINTS = (
    # fa — exact/full-text requests
    "عین متن", "متن کامل", "کل متن", "همه متن", "کلمه به کلمه",
    "متنش را کامل", "متنش رو کامل", "کلش رو بفرست", "کلش را بفرست",
    "بفرست",  # "X را بفرست" with a filename = verbatim send
    # en
    "exact text", "full text", "entire text", "verbatim", "send me the",
    "send it",
)

VERBATIM_MAX_CHARS = 15000


def wants_verbatim(message: str) -> bool:
    nq = f" {(message or '').lower()} "
    return any(h.lower() in nq for h in VERBATIM_HINTS)


def build_verbatim_answer(db, workspace_id, kb_ids, global_kb_ids,
                          message: str) -> dict | None:
    """Deterministic exact-text answer (no LLM): find the named file,
    return its transcript VERBATIM (capped). None when no intent/file."""
    if not wants_verbatim(message):
        return None
    hit = read_named_file(db, workspace_id, kb_ids, global_kb_ids, message)
    if hit is None:
        return None
    text = hit["text"]
    truncated = len(text) > VERBATIM_MAX_CHARS
    if truncated:
        text = text[:VERBATIM_MAX_CHARS]
    answer = (
        f"متن کامل «{hit['filename']}»"
        f"{' (بخش اول — ادامه در بخش فایل‌ها)' if truncated else ''}:\n\n"
        f"{text}"
    )
    return {"answer": answer, "filename": hit["filename"],
            "source_id": hit["id"], "truncated": truncated}


def wants_file_list(message: str) -> bool:
    nq = f" {(message or '').lower()} "
    if any(h.lower() in nq for h in _LIST_HINTS):
        return True
    if any(h.lower() in nq for h in _LIST_WEAK_HINTS):
        return any(w in nq for w in _LIST_CONTEXT_WORDS)
    return False


def _file_record(src) -> dict:
    return {
        "id": str(src.id),
        "filename": src.filename or "",
        "type": src.type or "",
        "status": src.status or "",
        "size_bytes": src.size_bytes or 0,
    }


def list_kb_files(db, workspace_id, kb_ids, global_kb_ids=None) -> list[dict]:
    """COMPLETE source inventory across the agent's workspace KBs (+
    assigned global KBs): no top-k truncation, no guessing."""
    from app.knowledge.models import Source

    kids = [coerce_uuid(k) for k in (kb_ids or [])]
    gkids = [coerce_uuid(k) for k in (global_kb_ids or [])]
    q = db.query(Source)
    out = []
    if kids:
        out += q.filter(Source.workspace_id == coerce_uuid(workspace_id),
                        Source.kb_id.in_(kids)).all()
    if gkids:
        out += q.filter(Source.workspace_id.is_(None),
                        Source.kb_id.in_(gkids)).all()
    if not kids and not gkids:
        # No KB scoping at all: whole workspace (still tenant-scoped).
        out += q.filter(
            Source.workspace_id == coerce_uuid(workspace_id)).all()
    seen: set[str] = set()
    records = []
    for src in sorted(out, key=lambda s: (s.filename or "")):
        if str(src.id) in seen:
            continue
        seen.add(str(src.id))
        records.append(_file_record(src))
    return records


def _filename_tokens(filename: str) -> set[str]:
    toks = set(fa_tokens(filename or ""))
    # extension-insensitive: "Executive Summary.docx" also matches "Summary"
    base = (filename or "").rsplit(".", 1)[0]
    toks |= set(fa_tokens(base))
    return {t for t in toks if len(t) > 2}


def find_named_files(files: list[dict], query: str) -> list[tuple[dict, int]]:
    """All filename matches ranked (file, overlap). Empty when none."""
    qtoks = set(fa_tokens(query))
    if not qtoks or not files:
        return []
    ranked = []
    for f in files:
        n = len(_filename_tokens(f.get("filename") or "") & qtoks)
        if n >= 1:
            ranked.append((f, n))
    ranked.sort(key=lambda t: t[1], reverse=True)
    return ranked


def find_named_file(files: list[dict], query: str) -> dict | None:
    """Best filename match for the query (normalized token overlap).

    Returns the file dict or None. Extension-insensitive, order-free.
    """
    ranked = find_named_files(files, query)
    return ranked[0][0] if ranked else None


def ambiguity_options(files: list[dict], query: str,
                      limit: int = 4) -> list[dict] | None:
    """Tied filename matches (unique names) or None.

    Used for the deterministic disambiguation question: when two or more
    files tie for the best token overlap, guessing would be wrong, so the
    agent asks instead. Returns [{filename, type}] or None.
    """
    ranked = find_named_files(files, query)
    if len(ranked) < 2 or ranked[0][1] != ranked[1][1] or ranked[0][1] < 1:
        return None
    seen: set[str] = set()
    out = []
    for f, _ in ranked:
        if f.get("filename") not in seen:
            seen.add(f["filename"])
            out.append({"filename": f["filename"], "type": f.get("type") or ""})
        if len(out) >= max(2, limit):
            break
    return out if len(out) >= 2 else None


def ambiguity_question(options: list[dict]) -> str:
    lines = ["کدام فایل مدنظرت است؟"]
    lines += [f"{i}. {o['filename']}" for i, o in enumerate(options, 1)]
    lines.append("شماره یا اسمش را بگو تا از همان جواب بدهم.")
    return "\n".join(lines)


def read_named_file(db, workspace_id, kb_ids, global_kb_ids, query: str,
                    storage=None) -> dict | None:
    """Full extracted text of the query-named file (capped), or None.

    Assembles from segments (same provenance as the transcript view) so
    the LLM gets the ORIGINAL text for translate/summarize/quote.
    """
    files = list_kb_files(db, workspace_id, kb_ids, global_kb_ids)
    target = find_named_file(files, query)
    if target is None:
        return None
    from app.knowledge.models import Document, PageSegment, Source

    src = (db.query(Source)
           .filter(Source.id == coerce_uuid(target["id"])).first())
    if src is None:
        return None
    docs = (db.query(Document).filter(Document.source_id == src.id)
            .order_by(Document.created_at).all())
    parts = []
    for doc in docs:
        segs = (db.query(PageSegment)
                .filter(PageSegment.document_id == doc.id).all())
        segs.sort(key=lambda s: (s.page_no or 0, s.start_ms or 0))
        for s in segs:
            text = (s.text or "").strip()
            if text:
                parts.append(text)
    text = "\n\n".join(parts).strip()
    if not text:
        return None
    truncated = len(text) > FILE_TEXT_MAX_CHARS
    if truncated:
        text = text[:FILE_TEXT_MAX_CHARS] + "\n…(truncated)"
    return {"id": str(src.id), "filename": src.filename or "",
            "type": src.type or "", "status": src.status or "",
            "text": text, "truncated": truncated}
