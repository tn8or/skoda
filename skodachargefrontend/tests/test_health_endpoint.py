"""Test cases for the health/rawlogs/age endpoint."""

import datetime
import importlib
import importlib.util
import json
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest


def load_frontend_with_health_stubs():
    """Load the frontend module with stubs that simulate different database states."""
    here = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    # Prepare stub modules to avoid real deps
    sys.modules.setdefault("mariadb", types.SimpleNamespace())
    sys.modules.setdefault("graypy", types.SimpleNamespace(GELFTCPHandler=object))

    # Create a stub 'commons' module
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

    def load_secret(_name):
        return None

    # This will be set per test
    _mock_db_state = {}

    class MockCursor:
        def execute(self, query, *args, **kwargs):
            if "MAX(log_timestamp)" in query:
                # Use the mock state to return appropriate data
                if _mock_db_state.get("raise_exception"):
                    raise Exception("Database error")
                return None

        def fetchone(self):
            if _mock_db_state.get("no_data"):
                return None
            if _mock_db_state.get("return_timestamp"):
                return [_mock_db_state["return_timestamp"]]
            return [datetime.datetime.now(datetime.timezone.utc)]

    class MockConn:
        auto_reconnect = True

        def cursor(self):
            return MockCursor()

    async def db_connect(_logger):
        return MockConn(), MockCursor()

    commons_stub.get_logger = get_logger
    commons_stub.db_connect = db_connect
    commons_stub.load_secret = load_secret

    # Store reference to mock state for tests to modify
    commons_stub._mock_db_state = _mock_db_state

    # Inject the stub into sys.modules
    prev_commons = sys.modules.get("commons")
    sys.modules["commons"] = commons_stub

    # Add frontend folder to sys.path
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
        return mod, commons_stub
    finally:
        # Restore previous commons module
        if prev_commons is not None:
            sys.modules["commons"] = prev_commons
        else:
            sys.modules.pop("commons", None)
        if path_added:
            try:
                sys.path.remove(here)
            except ValueError:
                pass


class TestHealthRawlogsAge:
    """Test cases for the /health/rawlogs/age endpoint."""

    @pytest.mark.asyncio
    async def test_no_rawlogs_found(self):
        """Test when no rawlogs are found in the database."""
        mod, commons = load_frontend_with_health_stubs()
        
        # Configure mock to return no data
        commons._mock_db_state["no_data"] = True
        
        response = await mod.latest_rawlog_age(threshold_seconds=60)
        
        assert response.status_code == 404
        content = json.loads(response.body)
        assert content["message"] == "no rawlogs found"
        assert content["latest_timestamp"] is None
        assert content["age_seconds"] is None
        assert content["threshold_seconds"] == 60

    @pytest.mark.asyncio
    async def test_database_error(self):
        """Test when database query fails."""
        mod, commons = load_frontend_with_health_stubs()
        
        # Configure mock to raise exception
        commons._mock_db_state["raise_exception"] = True
        
        response = await mod.latest_rawlog_age(threshold_seconds=60)
        
        assert response.status_code == 500
        content = json.loads(response.body)
        assert "error" in content
        assert "database error fetching rawlogs" in content["error"]

    @pytest.mark.asyncio
    async def test_within_threshold(self):
        """Test when latest event is within threshold."""
        mod, commons = load_frontend_with_health_stubs()
        
        # Configure mock to return recent timestamp (within threshold)
        recent_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=30)
        commons._mock_db_state["return_timestamp"] = recent_time
        
        response = await mod.latest_rawlog_age(threshold_seconds=60)
        
        # Should return 200 (normal dict response, not JSONResponse)
        assert hasattr(response, "keys")  # It's a dict
        assert response["within_threshold"] is True
        assert response["threshold_seconds"] == 60
        assert response["age_seconds"] <= 60

    @pytest.mark.asyncio
    async def test_exceeds_threshold_out_of_bounds(self):
        """Test when latest event exceeds threshold - should return 'out of bounds' message."""
        mod, commons = load_frontend_with_health_stubs()
        
        # Configure mock to return old timestamp (exceeds threshold)
        old_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=120)
        commons._mock_db_state["return_timestamp"] = old_time
        
        response = await mod.latest_rawlog_age(threshold_seconds=60)
        
        assert response.status_code == 503
        content = json.loads(response.body)
        assert content["message"] == "out of bounds"
        assert content["within_threshold"] is False
        assert content["threshold_seconds"] == 60
        assert content["age_seconds"] > 60

    @pytest.mark.asyncio
    async def test_no_threshold_provided(self):
        """Test when no threshold is provided."""
        mod, commons = load_frontend_with_health_stubs()
        
        # Configure mock to return a timestamp
        some_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=30)
        commons._mock_db_state["return_timestamp"] = some_time
        
        response = await mod.latest_rawlog_age(threshold_seconds=None)
        
        # Should return 200 (normal dict response)
        assert hasattr(response, "keys")  # It's a dict
        assert response["within_threshold"] is None
        assert response["threshold_seconds"] is None
        assert response["age_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_exactly_at_threshold(self):
        """Test when latest event is exactly at the threshold."""
        mod, commons = load_frontend_with_health_stubs()
        
        # Configure mock to return timestamp exactly at threshold
        threshold_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=60)
        commons._mock_db_state["return_timestamp"] = threshold_time
        
        response = await mod.latest_rawlog_age(threshold_seconds=60)
        
        # Should return 200 since age_seconds <= threshold_seconds
        assert hasattr(response, "keys")  # It's a dict
        assert response["within_threshold"] is True
        assert response["threshold_seconds"] == 60