"""
Test configuration for skodaupdatechargeprices tests.
Ensures the repository root is on sys.path so `import skodaupdatechargeprices.*` works
when running `pytest` from the `skodaupdatechargeprices/` folder.
Also provides minimal env + graypy stubs so get_logger works at import time.
"""

import os
import sys
from pathlib import Path
import types
import logging

# Add repo root (parent of the 'skodaupdatechargeprices' package) to sys.path
REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Minimal environment variables for logger
os.environ.setdefault("env", "test")
os.environ.setdefault("GRAYLOG_HOST", "localhost")
os.environ.setdefault("GRAYLOG_PORT", "12201")

# Stub graypy to avoid requiring the dependency in unit tests
if "graypy" not in sys.modules:
    class _DummyHandler(logging.Handler):
        def __init__(self, _host: str | None = None, _port: int | None = None, level: int | None = None):
            # Initialize with NOTSET to ensure .level exists; accept host/port like real GELFTCPHandler
            super().__init__(level=level or logging.NOTSET)

        def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
            # No-op: discard logs in tests
            return

    sys.modules["graypy"] = types.SimpleNamespace(GELFTCPHandler=_DummyHandler)
