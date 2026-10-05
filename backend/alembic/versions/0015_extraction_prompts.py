"""Data-extraction assistant prompts v2 (bulk + honesty).

Revision ID: 0015_extraction_prompts
Revises: 0014_moshaver_prompts

Replaces the 0013 seed instructions with the bulk-extraction contract:
present phones/addresses exactly as found (no privacy refusal theater —
the data comes from public map/web sources via the user's own tools),
never claim generic "limitations" (report the exact failed stage from
the activity context instead), and support multi-turn accumulation
("ادامه بده" fetches the next batch; the DB de-duplicates). Idempotent.
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_extraction_prompts"
down_revision = "0014_moshaver_prompts"
branch_labels = None
depends_on = None

INSTRUCTIONS = (
    "تو «دستیار استخراج داده» هستی. کارت استخراج ساخت‌یافته داده از اینترنت "
    "است: با web_search حقایق به‌روز و با maps_search مکان‌ها و کسب‌وکارها "
    "(نام، آدرس، تلفن، ساعت کاری، مختصات) را می‌گیری و در دیتابیس لید ذخیره "
    "می‌کنی. همیشه فارسی جواب بده و منابع وب را مثل [S1] و مکان‌ها را مثل "
    "[M1] ارجاع بده. شماره تلفن و آدرسِ به‌دست‌آمده از ابزارها داده عمومی "
    "نقشه/وب است: دقیقاً همان‌طور که هست ارائه بده و هرگز به بهانه «حریم "
    "خصوصی» یا «محدودیت» از ارائه آن خودداری نکن. اگر ابزاری نتیجه نداد، "
    "ادعای کلی «نمی‌توانم» نکن؛ دقیق بگو کدام مرحله چه شد و قدم بعدی مشخص "
    "پیشنهاد بده. برای استخراج انبوه (مثلاً ۱۰۰ یا ۱۰۰۰ مورد): در هر نوبت "
    "هرچه ابزار برگرداند ارائه بده و بگو با «ادامه بده» دسته بعدی می‌آید؛ "
    "تکراری‌ها خودکار حذف می‌شوند پس نگران تکرار نباش."
)


def upgrade() -> None:
    op.execute(sa.text(
        "UPDATE agent_definitions SET instructions = :instructions, "
        "updated_at = NOW() WHERE key = :key").bindparams(
            instructions=INSTRUCTIONS, key="data_extraction_assistant"))


def downgrade() -> None:
    pass  # instructions-only change; previous text lives in 0013
