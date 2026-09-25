"""In-process sliding-window rate limiter (single-instance MVP).

Limits are per (scope, client-key). For multi-worker deployments replace
the store with Redis without changing the decorator contract.
"""

import time
from collections import defaultdict
from functools import wraps

from fastapi import HTTPException, Request

_windows: dict[tuple[str, str], list[float]] = defaultdict(list)


def _client_key(request: Request) -> str:
    if request.client:
        return request.client.host
    return "unknown"


def check(scope: str, key: str, calls: int, period_seconds: int) -> None:
    now = time.monotonic()
    window = _windows[(scope, key)]
    cutoff = now - period_seconds
    while window and window[0] < cutoff:
        window.pop(0)
    if len(window) >= calls:
        raise HTTPException(429, "rate limit exceeded, try again shortly")
    window.append(now)


def limit(scope: str, calls: int, period_seconds: int = 60):
    """Decorator for endpoints: uses client IP (+ user when available)."""

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            request = kwargs.get("request")
            key = _client_key(request) if request else "unknown"
            check(scope, key, calls, period_seconds)
            return fn(*args, **kwargs)

        return wrapper

    return deco


def reset() -> None:
    """Tests only."""
    _windows.clear()
