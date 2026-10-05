"""Persian text normalization (single source of truth).

Maps Arabic codepoints to Persian, strips tashkeel/tatweel, and turns
ZWNJ into a space so token matching is stable across fonts/keyboards:

- ي → ی, ك → ک, ة → ه
- U+064B–U+0652 (tashkeel) + U+0670 removed
- U+0640 (tatweel) removed, U+200C (ZWNJ) → space

The Postgres backfill (migration 0017) mirrors this EXACTLY with
``translate(replace(replace(content, 'ـ', ''), '‌', ' '),
'يكه<diacritics>', 'یکه')`` — keep both in sync when changing anything
here. No lowercasing, no whitespace collapsing (1:1 with the SQL).
"""

_AR_TO_FA = str.maketrans({"ي": "ی", "ك": "ک", "ة": "ه"})

_STRIP = "".join([
    "ـ",  # U+0640 tatweel
    "ً", "ٌ", "ٍ", "َ", "ُ", "ِ", "ّ", "ْ",  # U+064B–U+0652
    "ٰ",  # U+0670 superscript alef
])


def normalize_fa(text: str | None) -> str:
    """Normalize Persian text (idempotent, 1:1 with migration 0017 SQL)."""
    if not text:
        return ""
    s = str(text).translate(_AR_TO_FA)
    for ch in _STRIP:
        s = s.replace(ch, "")
    return s.replace("‌", " ")


def fa_tokens(text: str | None) -> list[str]:
    """Lowercased normalized tokens (len > 1) for matching/scoring."""
    return [t for t in normalize_fa(text).lower().split() if len(t) > 1]
