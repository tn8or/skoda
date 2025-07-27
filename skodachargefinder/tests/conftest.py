"""
Shared test configuration and fixtures for the test suite.

This module provides common fixtures and configuration for all test modules
in the project, ensuring consistent test setup and teardown.
"""

import asyncio
import os
import sys
from typing import Any, Dict
from unittest.mock import Mock, patch

import pytest

# Add the parent directory (containing chargefinder.py) to Python path
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment() -> Mock:
    """
    Set up the test environment before any imports.

    This fixture runs once per test session and ensures that
    environment variables and global mocks are properly configured
    before any modules are imported.

    Returns:
        Mock: The mocked logger instance for use in tests.
    """
    # Set environment variables for testing
    os.environ.setdefault("env", "test")
    os.environ.setdefault("GRAYLOG_HOST", "localhost")
    os.environ.setdefault("GRAYLOG_PORT", "12201")
    os.environ.setdefault("MARIADB_HOSTNAME", "localhost")
    os.environ.setdefault("MARIADB_DATABASE", "test_db")
    os.environ.setdefault("MARIADB_USERNAME", "test_user")
    os.environ.setdefault("MARIADB_PASSWORD", "test_pass")

    # Mock the logger initialization to prevent issues during import
    with patch("commons.get_logger") as mock_get_logger:
        mock_logger = Mock()
        mock_get_logger.return_value = mock_logger

        # Mock asyncio operations that might be called during import
        with patch("asyncio.create_task") as mock_create_task:
            mock_task = Mock()
            mock_create_task.return_value = mock_task

            yield mock_logger


@pytest.fixture
def sample_charge_data() -> Dict[str, str]:
    """
    Provide sample charge data for testing.

    Returns:
        Dict[str, str]: Sample charge event data with realistic values that
                       can be used across multiple test cases for consistency.
    """
    return {
        "timestamp": "2025-07-25 10:00:00",
        "pos_lat": "55.547873",
        "pos_lon": "11.22252",
        "charged_range": "327",
        "mileage": "82554",
        "event_type": "start",
        "soc": "79",
    }


@pytest.fixture
def sample_log_messages() -> Dict[str, str]:
    """
    Provide sample log messages for parsing tests.

    Returns:
        Dict[str, str]: Various log message formats that represent real-world
                       log data for testing parsing functions.
    """
    return {
        "charging_start": "EventCharging(...ChargingState.CHARGING...soc=79,charged_range=327...)",
        "charging_stop": "EventCharging(...ChargingState.READY_FOR_CHARGING...soc=85,charged_range=350...)",
        "stop_command": "EventCharging(...OperationName.STOP_CHARGING...)",
        "position": "Vehicle positions fetched: lat: 55.547873, lng: 11.22252",
        "mileage": "Vehicle health fetched, mileage: 82554",
    }


@pytest.fixture
def event_loop():
    """
    Create an event loop for async tests.

    This fixture ensures that each test gets its own event loop,
    preventing issues with async test execution and cleanup.

    Yields:
        asyncio.AbstractEventLoop: A new event loop for the test.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
