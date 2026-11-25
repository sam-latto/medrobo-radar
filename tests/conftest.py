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


def _refuse(*args, **kwargs):
    raise RuntimeError("Test attempted a real Claude API call; fake call_tool / call_tools or client.messages")


class _NoBatches:
    create = cancel = retrieve = results = staticmethod(_refuse)


class _NoMessages:
    create = staticmethod(_refuse)

    def __init__(self):
        self.batches = _NoBatches()


class _NoClaude:
    """Stands in for the Anthropic client so a test can never make a real (paid) Claude call.
    Tests can still replace client.messages.create / .batches with fakes, as before."""

    def __init__(self):
        self.messages = _NoMessages()


@pytest.fixture(autouse=True)
def _no_claude(monkeypatch):
    from pipeline import llm
    monkeypatch.setattr(llm, "client", _NoClaude())
