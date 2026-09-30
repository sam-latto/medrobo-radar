import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Point the app at a throwaway DB before any project module reads config.
_tmpdir = tempfile.mkdtemp(prefix="radar-tests-")
os.environ["DB_PATH"] = os.path.join(_tmpdir, "test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


import pytest
import requests


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Tests use saved fixtures; fail loudly if anything tries a real HTTP request."""
    def blocked(self, method, url, *args, **kwargs):
        raise RuntimeError(f"Test attempted a live HTTP request: {method} {url}")
    monkeypatch.setattr(requests.Session, "request", blocked)
