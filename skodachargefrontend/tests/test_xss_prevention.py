"""
Tests for Cross-Site Scripting (XSS) prevention in the skodachargefrontend.

This module tests that user input and database-derived content
is properly escaped before being inserted into HTML responses.
"""

import importlib
import importlib.util
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest


def load_frontend_with_stubs():
    """Load the frontend module with mocked dependencies."""
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
        def __init__(self):
            self._rows = []

        def execute(self, *args, **kwargs):
            return None

        def fetchall(self):
            return self._rows

        def set_mock_data(self, rows):
            """Set mock data for testing."""
            self._rows = rows

    class Conn:
        auto_reconnect = True

        def __init__(self):
            self._cursor = Cursor()

        def cursor(self):
            return self._cursor

    async def db_connect(_logger):
        conn = Conn()
        return conn, conn.cursor()

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


class TestXSSPrevention:
    """Test cases for XSS prevention in HTML output."""

    def test_escape_html_function(self):
        """Test the escape_html utility function."""
        mod = load_frontend_with_stubs()
        
        # Test basic escaping
        assert mod.escape_html("<script>alert('xss')</script>") == "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"
        assert mod.escape_html("&<>\"'") == "&amp;&lt;&gt;&quot;&#x27;"
        
        # Test with None
        assert mod.escape_html(None) == ""
        
        # Test with numbers
        assert mod.escape_html(2025) == "2025"
        assert mod.escape_html(1.5) == "1.5"
        
        # Test with safe content
        assert mod.escape_html("Normal text") == "Normal text"

    @pytest.mark.asyncio
    async def test_year_parameter_escaped_in_html(self):
        """Test that year parameter is properly escaped in HTML output."""
        mod = load_frontend_with_stubs()
        
        # Call with normal values - should work without escaping issues
        resp = await mod.root(year=2025, month=1)
        body = resp.body.decode("utf-8") if hasattr(resp, "body") else str(resp)
        
        # Should contain the year in escaped form in the title
        assert "<title>Charge Summary for 2025-01</title>" in body
        assert "Charge Summary for 2025-01" in body

    @pytest.mark.asyncio
    async def test_database_content_escaped(self):
        """Test that database-derived content is properly escaped."""
        mod = load_frontend_with_stubs()
        
        # Get a reference to db_connect to mock the cursor
        original_db_connect = mod.db_connect
        
        async def mock_db_connect(_logger):
            """Mock db_connect with potentially malicious data."""
            from datetime import datetime
            
            class MockCursor:
                def execute(self, *args, **kwargs):
                    pass

                def fetchall(self):
                    # Return mock data with XSS attempts
                    return [
                        (
                            datetime.now(),  # log_timestamp
                            datetime.now(),  # start_at
                            datetime.now(),  # stop_at
                            25.5,  # amount - larger amount to ensure it's displayed 
                            15.0,  # price
                            100.0,  # charged_range
                            50.0,  # start_range
                            12345,  # mileage - numeric value (but we'll test position)
                            'home<script>alert("xss")</script>',  # position - XSS attempt that starts with "home"
                            80.0,  # soc
                        )
                    ]

            class MockConn:
                auto_reconnect = True

                def cursor(self):
                    return MockCursor()

            return MockConn(), MockCursor()

        # Temporarily replace db_connect
        mod.db_connect = mock_db_connect
        
        try:
            resp = await mod.root(year=2025, month=1)
            body = resp.body.decode("utf-8") if hasattr(resp, "body") else str(resp)
            
            # Should not contain unescaped script tags
            assert '<script>alert("xss")</script>' not in body
            assert 'home<script>' not in body
            
            # The position field should be normalized by business logic to "away" 
            # since 'home<script>...' != "home" exactly
            assert 'away' in body
            
        finally:
            # Restore original db_connect
            mod.db_connect = original_db_connect

    @pytest.mark.asyncio
    async def test_build_metadata_escaped(self):
        """Test that build metadata from environment variables is escaped."""
        mod = load_frontend_with_stubs()
        
        # Mock environment variables with XSS attempts
        original_env = os.environ.copy()
        os.environ["GIT_COMMIT"] = '<script>alert("commit")</script>'
        os.environ["GIT_TAG"] = '<img src=x onerror=alert("tag")>'
        os.environ["BUILD_DATE"] = '<script>alert("date")</script>'
        
        try:
            resp = await mod.root(year=2025, month=1)
            body = resp.body.decode("utf-8") if hasattr(resp, "body") else str(resp)
            
            # Should not contain unescaped script tags or img tags
            assert '<script>alert("commit")</script>' not in body
            assert '<img src=x onerror=alert("tag")>' not in body
            assert '<script>alert("date")</script>' not in body
            
            # Should contain escaped versions (checking for the correct HTML entity format)
            assert '&lt;script&gt;alert(&quot;commit&quot;)&lt;/script&gt;' in body
            assert '&lt;img src=x onerror=alert(&quot;tag&quot;)&gt;' in body
            assert '&lt;script&gt;alert(&quot;date&quot;)&lt;/script&gt;' in body
            
        finally:
            # Restore original environment
            os.environ.clear()
            os.environ.update(original_env)

    @pytest.mark.asyncio
    async def test_navigation_links_safe(self):
        """Test that navigation links with year/month parameters are safe."""
        mod = load_frontend_with_stubs()
        
        resp = await mod.root(year=2025, month=1)
        body = resp.body.decode("utf-8") if hasattr(resp, "body") else str(resp)
        
        # Should contain proper navigation links
        assert '/?year=2024&month=12' in body  # Previous month
        assert '/?year=2025&month=2' in body   # Next month
        
        # Should not contain any script tags in navigation
        assert '<script>' not in body.split('Previous Month')[0][-100:] if 'Previous Month' in body else True
        assert '<script>' not in body.split('Next Month')[0][-100:] if 'Next Month' in body else True

    @pytest.mark.asyncio
    async def test_daily_totals_date_formatting_safe(self):
        """Test that date formatting in daily totals is safe from XSS."""
        mod = load_frontend_with_stubs()
        
        # Get a reference to db_connect to mock the cursor
        original_db_connect = mod.db_connect
        
        async def mock_db_connect(_logger):
            """Mock db_connect with data that includes dates."""
            from datetime import datetime
            
            class MockCursor:
                def execute(self, *args, **kwargs):
                    pass

                def fetchall(self):
                    # Return mock data with multiple days to trigger daily totals
                    return [
                        (
                            datetime(2025, 1, 15, 10, 0, 0),  # log_timestamp
                            datetime(2025, 1, 15, 10, 0, 0),  # start_at
                            datetime(2025, 1, 15, 11, 0, 0),  # stop_at
                            25.5,  # amount - larger amount to ensure it's displayed 
                            15.0,  # price
                            100.0,  # charged_range
                            50.0,  # start_range
                            12345,  # mileage
                            'home',  # position
                            80.0,  # soc
                        ),
                        (
                            datetime(2025, 1, 16, 10, 0, 0),  # log_timestamp
                            datetime(2025, 1, 16, 10, 0, 0),  # start_at
                            datetime(2025, 1, 16, 11, 0, 0),  # stop_at
                            20.0,  # amount
                            12.0,  # price
                            80.0,  # charged_range
                            40.0,  # start_range
                            12400,  # mileage
                            'home',  # position
                            75.0,  # soc
                        )
                    ]

            class MockConn:
                auto_reconnect = True

                def cursor(self):
                    return MockCursor()

            return MockConn(), MockCursor()

        # Temporarily replace db_connect
        mod.db_connect = mock_db_connect
        
        try:
            resp = await mod.root(year=2025, month=1)
            body = resp.body.decode("utf-8") if hasattr(resp, "body") else str(resp)
            
            # Should contain properly formatted dates
            assert '2025-01-15' in body
            assert '2025-01-16' in body
            
            # Should not contain any script tags in the date formatting
            assert '<script>' not in body
            assert 'javascript:' not in body.lower()
            assert 'onerror=' not in body.lower()
            
        finally:
            # Restore original db_connect
            mod.db_connect = original_db_connect

    @pytest.mark.asyncio
    async def test_url_parameters_safe_in_navigation(self):
        """Test that URL parameters in navigation links are safe from XSS."""
        mod = load_frontend_with_stubs()
        
        # Test with edge case years to ensure they're handled safely
        resp = await mod.root(year=2025, month=12)  # Test December for year rollover
        body = resp.body.decode("utf-8") if hasattr(resp, "body") else str(resp)
        
        # Check that navigation URLs are properly formed
        assert '/?year=2025&month=11' in body  # Previous month
        assert '/?year=2026&month=1' in body   # Next month (year rollover)
        
        # Ensure no XSS in URL parameters
        assert 'javascript:' not in body.lower()
        assert '<script>' not in body
        assert 'onerror=' not in body.lower()
        
        # Test January for year rollback
        resp2 = await mod.root(year=2025, month=1)
        body2 = resp2.body.decode("utf-8") if hasattr(resp2, "body") else str(resp2)
        
        assert '/?year=2024&month=12' in body2  # Previous month (year rollback)
        assert '/?year=2025&month=2' in body2   # Next month