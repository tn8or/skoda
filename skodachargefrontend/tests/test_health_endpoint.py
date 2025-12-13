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
        def __init__(self):
            self._last_query_type = None

        def execute(self, query, *args, **kwargs):
            # Determine if this is a vehicle-specific or general query
            if "log_message LIKE" in query:
                # Vehicle-specific lookup (supports both MAX and ORDER BY ... LIMIT 1 forms)
                self._last_query_type = "vehicle"
                if _mock_db_state.get("vehicle_raise_exception"):
                    raise Exception("Database error on vehicle query")
            elif "MAX(log_timestamp)" in query:
                self._last_query_type = "general"
                if _mock_db_state.get("raise_exception"):
                    raise Exception("Database error on general query")
            else:
                self._last_query_type = None
            return None

        def fetchone(self):
            if self._last_query_type == "vehicle":
                # Return vehicle-specific data
                if _mock_db_state.get("vehicle_no_data"):
                    return None
                if _mock_db_state.get("vehicle_return_timestamp"):
                    return [_mock_db_state["vehicle_return_timestamp"]]
                # Default: return same as general if not specified
                if _mock_db_state.get("return_timestamp"):
                    return [_mock_db_state["return_timestamp"]]
                return [datetime.datetime.now(datetime.timezone.utc)]
            elif self._last_query_type == "general":
                # Return general data
                if _mock_db_state.get("no_data"):
                    return None
                if _mock_db_state.get("return_timestamp"):
                    return [_mock_db_state["return_timestamp"]]
                return [datetime.datetime.now(datetime.timezone.utc)]
            return None

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
        assert "vehicle_log_patterns" in content

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
        recent_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            seconds=30
        )
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
        old_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            seconds=120
        )
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
        some_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            seconds=30
        )
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
        threshold_time = datetime.datetime.now(
            datetime.timezone.utc
        ) - datetime.timedelta(seconds=60)
        commons._mock_db_state["return_timestamp"] = threshold_time

        response = await mod.latest_rawlog_age(threshold_seconds=60)

        # Should return 200 since age_seconds <= threshold_seconds
        assert hasattr(response, "keys")  # It's a dict
        assert response["within_threshold"] is True
        assert response["threshold_seconds"] == 60

    @pytest.mark.asyncio
    async def test_no_vehicle_telemetry_logs(self):
        """Test when general logs exist but no vehicle telemetry logs - should alert."""
        mod, commons = load_frontend_with_health_stubs()

        # Configure mock: general logs exist, but no vehicle logs
        general_time = datetime.datetime.now(
            datetime.timezone.utc
        ) - datetime.timedelta(seconds=30)
        commons._mock_db_state["return_timestamp"] = general_time
        commons._mock_db_state["vehicle_no_data"] = True

        response = await mod.latest_rawlog_age(threshold_seconds=60)

        # Should return 503 because vehicle logs are missing
        assert response.status_code == 503
        content = json.loads(response.body)
        assert content["message"] == "no vehicle telemetry logs found"
        assert content["latest_timestamp"] is None
        assert content["latest_general_timestamp"] is not None
        assert "vehicle_log_patterns" in content
        assert len(content["vehicle_log_patterns"]) > 0

    @pytest.mark.asyncio
    async def test_vehicle_logs_older_than_general(self):
        """Test when vehicle logs are significantly older than general logs - should alert."""
        mod, commons = load_frontend_with_health_stubs()

        # General logs are recent (service is running)
        general_time = datetime.datetime.now(
            datetime.timezone.utc
        ) - datetime.timedelta(seconds=30)
        # Vehicle logs are very old (car not communicating for a month)
        vehicle_time = datetime.datetime.now(
            datetime.timezone.utc
        ) - datetime.timedelta(days=30)

        commons._mock_db_state["return_timestamp"] = general_time
        commons._mock_db_state["vehicle_return_timestamp"] = vehicle_time

        response = await mod.latest_rawlog_age(threshold_seconds=3600)

        # Should return 503 because vehicle logs exceed threshold
        assert response.status_code == 503
        content = json.loads(response.body)
        assert content["message"] == "out of bounds"
        assert content["age_seconds"] > 3600
        assert content["general_age_seconds"] < 3600
        assert content["within_threshold"] is False


class TestHealthEndpointHTTPMethods:
    """Test cases for HTTP method support on the health endpoint."""

    def test_endpoint_supports_get_and_head_methods(self):
        """Test that the health endpoint is configured to support both GET and HEAD methods."""
        import re

        # Read the source file to verify the route definition
        here = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        frontend_file = os.path.join(here, "skodachargefrontend.py")

        with open(frontend_file, "r") as f:
            content = f.read()

        # Verify the health endpoint supports both GET and HEAD methods
        health_route_pattern = r'@app\.api_route\([^)]*"/health/rawlogs/age"[^)]*methods=\[.*"GET".*"HEAD".*\][^)]*\)'
        match = re.search(health_route_pattern, content)

        assert (
            match is not None
        ), "Health endpoint should be configured with @app.api_route supporting GET and HEAD"

        # Verify both methods are present
        route_def = match.group(0)
        assert '"GET"' in route_def, "Health endpoint should support GET method"
        assert (
            '"HEAD"' in route_def
        ), "Health endpoint should support HEAD method for UptimeRobot compatibility"
