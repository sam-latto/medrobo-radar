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
