import datetime
import importlib.util
import os
import sys
import types

import pytest


class UnknownColumnError(Exception):
    pass


def load_frontend_with_legacy_schema_stub():
    here = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    prev_mariadb = sys.modules.get("mariadb")
    prev_graypy = sys.modules.get("graypy")
    sys.modules.setdefault("mariadb", types.SimpleNamespace())
    sys.modules.setdefault("graypy", types.SimpleNamespace(GELFTCPHandler=object))

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
        def __init__(self):
            self._raised = False

        def execute(self, query, *args, **kwargs):
            # Fail only on the first modern query that references start_range.
            if (
                "start_range" in query
                and "NULL AS start_range" not in query
                and not self._raised
            ):
                self._raised = True
                raise UnknownColumnError(
                    1054, "Unknown column 'start_range' in 'SELECT'"
                )
            return None

        def fetchall(self):
            return [
                (
                    datetime.datetime(2026, 4, 19, 12, 0, 0),
                    datetime.datetime(2026, 4, 19, 11, 0, 0),
                    datetime.datetime(2026, 4, 19, 12, 0, 0),
                    10.0,
                    25.0,
                    250,
                    None,
                    100000,
                    "55.5,11.2",
                    80,
                )
            ]

    class Conn:
        auto_reconnect = True

        def __init__(self):
            self._cursor = Cursor()

        def cursor(self):
            return self._cursor

    async def db_connect(_logger):
        conn = Conn()
        return conn, conn.cursor()

    commons_stub.get_logger = get_logger
    commons_stub.db_connect = db_connect

    prev_commons = sys.modules.get("commons")
    sys.modules["commons"] = commons_stub

    path_added = False
    if here not in sys.path:
        sys.path.insert(0, here)
        path_added = True

    try:
        app_path = os.path.join(here, "skodachargefrontend.py")
        spec = importlib.util.spec_from_file_location("frontend_app_legacy", app_path)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        return mod
    finally:
        if prev_mariadb is not None:
            sys.modules["mariadb"] = prev_mariadb
        else:
            sys.modules.pop("mariadb", None)
        if prev_graypy is not None:
            sys.modules["graypy"] = prev_graypy
        else:
            sys.modules.pop("graypy", None)
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
async def test_root_falls_back_when_start_range_column_missing():
    mod = load_frontend_with_legacy_schema_stub()
    resp = await mod.root(year=2026, month=4)
    assert resp.status_code == 200
    body = resp.body.decode("utf-8")
    assert "Charge Summary" in body
