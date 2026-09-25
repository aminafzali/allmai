"""Platform AI settings (managed from the admin panel).

Global table (no RLS, admin-gated). Values override ENV defaults;
resolution order everywhere: DB row -> ENV/code default.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base import Base, UUIDPrimaryKeyMixin, utcnow


class AISetting(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ai_settings"

    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
