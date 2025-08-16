import importlib
import importlib.util
import os
import sys
import types

import pytest


def load_frontend_with_stubs():
    here = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    # Prepare stub modules to avoid real deps and avoid polluting sys.path
    sys.modules.setdefault("mariadb", types.SimpleNamespace())
    sys.modules.setdefault("graypy", types.SimpleNamespace(GELFTCPHandler=object))

    # Create a stub 'commons' module providing only what's needed
    commons_stub = types.ModuleType("commons")

    class DummyLogger:
        def debug(self, *args, **kwargs):
            pass

        def info(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

        def error(self, *args, **kwargs):
            pass

    def get_logger(_name):
        return DummyLogger()

    class Cursor:
        def execute(self, *args, **kwargs):
            return None

        def fetchall(self):
            return []

    class Conn:
        auto_reconnect = True

        def cursor(self):
            return Cursor()

    async def db_connect(_logger):
        return Conn(), Cursor()

    def load_secret(_name):
        return None

    commons_stub.get_logger = get_logger
    commons_stub.db_connect = db_connect
    commons_stub.load_secret = load_secret

    # Inject the stub into sys.modules temporarily
    prev_commons = sys.modules.get("commons")
    sys.modules["commons"] = commons_stub
    # Temporarily add the frontend folder to sys.path so `from helpers import ...` resolves correctly
    path_added = False
    if here not in sys.path:
        sys.path.insert(0, here)
        path_added = True
    try:
        app_path = os.path.join(here, "skodachargefrontend.py")
        spec = importlib.util.spec_from_file_location("frontend_app", app_path)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        return mod
    finally:
        # Restore any previous 'commons' module to avoid side effects on other tests
        if prev_commons is not None:
            sys.modules["commons"] = prev_commons
        else:
            sys.modules.pop("commons", None)
        if path_added:
            try:
                sys.path.remove(here)
            except ValueError:
                pass


@pytest.mark.asyncio
async def test_root_renders_without_db():
    mod = load_frontend_with_stubs()
    # Call the endpoint coroutine directly to avoid spinning up ASGI server
    resp = await mod.root(year=2025, month=1)
    # HTMLResponse has body in .body or .media; FastAPI returns starlette Response
    body = resp.body.decode("utf-8") if hasattr(resp, "body") else str(resp)
    assert "Charge Summary" in body
    assert "No charge data found" in body
