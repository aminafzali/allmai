"""Backfill Persian normalization over ingested text (idempotent).

Revision ID: 0017_normalize_fa
Revises: 0016_conversation_titles

Mirrors app/knowledge/normalize.py EXACTLY (keep in sync):
  translate(replace(replace(col, TATWEEL, ''), ZWNJ, ' '), FROM, TO)
i.e. Arabic Yeh/Kaf/Teh-Marbuta -> Persian, tashkeel + superscript-alef
deleted, tatweel deleted, ZWNJ -> space. content_tsv is a PLAIN column
(the worker writes it explicitly), so it is recomputed here too.
Safe to re-run: normalization is idempotent.

Literal codepoints below are pinned by tests/test_retrieval_quality.py
(test_migration_literals_match_python).
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_normalize_fa"
down_revision = "0016_conversation_titles"
branch_labels = None
depends_on = None

NORM_FROM = "يكةًٌٍَُِّْٰ"
NORM_TO = "یکه"
TATWEEL = "ـ"
ZWNJ = "‌"


def _norm(col: str) -> str:
    return (
        f"translate(replace(replace({col}, '{TATWEEL}', ''), '{ZWNJ}', ' '), "
        f"'{NORM_FROM}', '{NORM_TO}')"
    )


def upgrade() -> None:
    op.execute(sa.text(
        f"UPDATE chunks SET content = {_norm('content')}, "
        f"content_tsv = to_tsvector('simple', {_norm('content')})"))
    # page_segments.text stays RAW (transcript fidelity); only the
    # search surfaces (chunk content) and display titles are normalized.
    op.execute(sa.text(
        f"UPDATE documents SET title = {_norm('title')}"))


def downgrade() -> None:
    pass  # normalization is lossy by design; cannot be reversed
