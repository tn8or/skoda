import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import skodaimporter.commons as m


@pytest.mark.asyncio
async def test_pull_api_json(monkeypatch):
    class DummyResp:
        def __init__(self):
            self._json = {"ok": True}
            self.text = "{}"

        def raise_for_status(self):
            return None

        def json(self):
            return self._json

    class DummyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            return DummyResp()

    # Inject a fake httpx module for the lazy import
    fake_httpx = types.SimpleNamespace(
        AsyncClient=DummyClient, RequestError=Exception, HTTPStatusError=Exception
    )
    sys.modules["httpx"] = fake_httpx
    logger = MagicMock()
    out = await m.pull_api("http://example", logger)
    assert out == {"ok": True}


@pytest.mark.asyncio
async def test_db_connect_missing_driver(monkeypatch):
    # Force mariadb to be None via reload hack
    with patch.object(m, "mariadb", None):
        logger = MagicMock()
        conn = await m.db_connect(logger)
        assert conn is False
