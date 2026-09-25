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
