"""Multi-turn long-document translation (chunked "continue" flow).

Why: a single answer is capped (~1500 tokens by default), so long
translations get cut. When a translate request targets a long text,
turn 1 translates piece 1 and stores the rest in conversation state;
"ادامه بده" serves the next piece. Deterministic piece splitting
(paragraph-aware); the LLM only translates, never re-segments.
"""

from __future__ import annotations

PIECE_CHARS = 5000
LONG_TEXT_CHARS = 6000
PIECE_MAX_TOKENS = 4000

TRANSLATE_HINTS = (
    "ترجمه", "ترجمه کن", "ترجمه‌اش", "translate", "translation",
)

CONTINUE_HINTS = (
    "ادامه بده", "ادامه", "بقیه", "بخش بعد", "قسمت بعد",
    "continue", "next", "بده بقیه",
)


def is_translate_request(message: str) -> bool:
    nq = f" {(message or '').lower()} "
    return any(h.lower() in nq for h in TRANSLATE_HINTS)


def is_continue(message: str) -> bool:
    nq = f" {(message or '').strip().lower()} "
    if len(nq) > 40:
        return False  # long messages are new requests, not continuations
    return any(h.lower() in nq for h in CONTINUE_HINTS)


def split_pieces(text: str, size: int = PIECE_CHARS) -> list[str]:
    """Split on paragraph boundaries into ~size-char pieces."""
    paras = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    if not paras:
        paras = [(text or "").strip()] if (text or "").strip() else []
    pieces, current = [], ""
    for p in paras:
        if current and len(current) + len(p) + 2 > size:
            pieces.append(current)
            current = p
        else:
            current = (current + "\n\n" + p) if current else p
    if current:
        pieces.append(current)
    # single giant paragraph without breaks: hard-split
    out: list[str] = []
    for piece in pieces:
        while len(piece) > size * 2:
            out.append(piece[:size])
            piece = piece[size:]
        out.append(piece)
    return [p for p in out if p]


def translate_prompt(piece: str, idx: int, total: int,
                     filename: str = "") -> str:
    where = f" فایل «{filename}»" if filename else ""
    return (
        f"متن زیر (بخش {idx} از {total}){where} را به فارسی روان ترجمه کن. "
        "فقط خود ترجمه را برگردان؛ بدون مقدمه، بدون توضیح اضافه، بدون "
        "خلاصه‌سازی:\n\n" + piece)
