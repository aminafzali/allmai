"""Password hashing + JWT helpers (HS256, access + refresh)."""

from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.core.config import get_settings

settings = get_settings()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str, minutes: int | None = None) -> str:
    exp = datetime.now(timezone.utc) + timedelta(
        minutes=minutes if minutes is not None else settings.ACCESS_TOKEN_MINUTES
    )
    return jwt.encode(
        {"sub": user_id, "type": "access", "exp": exp},
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> dict:
    """Return claims or raise JWTError (covers expiry)."""
    claims = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    if claims.get("type") != "access":
        raise JWTError("not an access token")
    return claims
