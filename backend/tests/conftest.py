import os
import sys
from pathlib import Path

import pytest

# Run pytest from backend/ : ensure `import app...` resolves.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("JWT_SECRET", "test-secret")


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Rate-limit windows are process-global: isolate every test."""
    from app.common.rate_limit import reset

    reset()
    yield
    reset()


@pytest.fixture(autouse=True)
def _pin_legacy_reranker(monkeypatch):
    """Hermetic suite: live API reranking must never fire in unit tests.

    Production default is api/avalai; tests pin the deterministic legacy
    heuristic so ranking assertions stay stable and offline. The new
    contract (none/api/local explicitly) is covered by dedicated tests
    that control settings themselves.
    """
    import app.ai.settings as _S

    _real = _S.list_settings

    def _ls(db=None):
        try:
            merged = dict(_real(db))
        except Exception:
            merged = {}
        merged["retrieval.rerank"] = {"method": "heuristic"}
        return merged

    monkeypatch.setattr(_S, "list_settings", _ls)
