"""
Test fixtures and configuration for the chargecollector test suite.

Provides common test data, mock objects, and test utilities.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# Add the parent directory to the path so we can import the module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock environment variables before importing modules
import unittest.mock

with unittest.mock.patch.dict(
    os.environ,
    {
        "env": "test",
        "GRAYLOG_HOST": "localhost",
        "GRAYLOG_PORT": "12201",
        "MARIADB_USERNAME": "test_user",
        "MARIADB_PASSWORD": "test_pass",
        "MARIADB_HOSTNAME": "localhost",
        "MARIADB_DATABASE": "test_db",
    },
):
    import mariadb
    from chargecollector import ChargeCollectorState, LocationConfig


@pytest.fixture
def collector_state():
    """Create a fresh ChargeCollectorState for testing."""
    return ChargeCollectorState()


@pytest.fixture
def location_config():
    """Create a LocationConfig for testing."""
    return LocationConfig(home_latitude="55.547", home_longitude="11.222")


@pytest.fixture
def mock_db_connection():
    """Mock database connection and cursor."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    return mock_conn, mock_cur


@pytest.fixture
def mock_db_connect():
    """Mock database connection for testing."""
    from unittest.mock import Mock

    mock_conn = Mock()
    mock_cur = Mock()

    # Set default return values
    mock_cur.fetchone.return_value = None
    mock_cur.lastrowid = 1

    with patch("chargecollector.db_connect") as mock:

        async def mock_db_connect_func(logger):
            return mock_conn, mock_cur

        mock.side_effect = mock_db_connect_func
        yield mock, mock_conn, mock_cur


@pytest.fixture
def mock_logger():
    """Mock logger for testing."""
    with patch("chargecollector.my_logger") as mock:
        yield mock


@pytest.fixture
def sample_charge_event():
    """Sample charge event data for testing."""
    return (
        1,  # id
        datetime(2024, 1, 15, 10, 30, 0),  # timestamp
        "start",  # event_type
        85.5,  # charged_range
        12345,  # mileage
        "55.547",  # latitude
        "11.222",  # longitude
        80,  # soc
    )


@pytest.fixture
def sample_stop_charge_event():
    """Sample stop charge event data for testing."""
    import datetime

    return (
        2,  # id
        datetime.datetime(2024, 1, 15, 12, 45, 0),  # timestamp
        "stop",  # event_type
        95.5,  # charged_range
        12347,  # mileage
        "55.547",  # latitude
        "11.222",  # longitude
        95,  # soc
    )


@pytest.fixture
def sample_away_charge_event():
    """Sample charge event at away location for testing."""
    return (
        3,  # id
        datetime.datetime(2024, 1, 15, 14, 30, 0),  # timestamp
        "start",  # event_type
        85.5,  # charged_range
        12350,  # mileage
        "56.123",  # latitude (different from home)
        "12.456",  # longitude (different from home)
        75,  # soc
    )


@pytest.fixture
def sample_charge_hour_row():
    """Sample charge hour database row for testing."""
    return (
        1,  # id
        datetime(2024, 1, 15, 10, 0, 0),  # log_timestamp
        datetime(2024, 1, 15, 10, 30, 0),  # start_at
        datetime(2024, 1, 15, 12, 45, 0),  # stop_at
        "home",  # position
        85.5,  # charged_range
        12345,  # mileage
        80,  # soc
        None,  # amount
        75.0,  # start_range
    )


@pytest.fixture
def sample_charge_hours():
    """Multiple sample charge hours for testing."""
    return [
        (
            1,
            datetime.datetime(2024, 1, 15, 10, 0, 0),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ),
        (
            2,
            datetime.datetime(2024, 1, 15, 11, 0, 0),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ),
        (
            3,
            datetime.datetime(2024, 1, 15, 12, 0, 0),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ),
    ]


@pytest.fixture
def mock_state_instances():
    """Mock the global state instances."""
    with patch("chargecollector._collector_state") as mock_collector, patch(
        "chargecollector._location_config"
    ) as mock_location:

        mock_collector.last_hour = ""
        mock_collector.still_going = False
        mock_collector.data_processed = 0

        mock_location.home_latitude = "55.547"
        mock_location.home_longitude = "11.222"

        yield mock_collector, mock_location


@pytest.fixture
def mock_commons_functions():
    """Mock functions from commons module."""
    with patch("chargecollector.SLEEPTIME", 30), patch(
        "chargecollector.UPDATECHARGES_URL", "http://test.com/update"
    ), patch("chargecollector.pull_api") as mock_pull_api:

        mock_pull_api.return_value = {"status": "success"}
        yield mock_pull_api


@pytest.fixture
def mock_mariadb_error():
    """Mock MariaDB error for testing error handling."""
    return mariadb.Error("Database connection failed")


@pytest.fixture
def mock_async_sleep():
    """Mock asyncio.sleep for testing."""
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock:
        yield mock


class MockDateTime:
    """Mock datetime class for testing."""

    @staticmethod
    def strptime(date_string: str, format_string: str) -> datetime:
        """Mock strptime method."""
        if date_string == "2024-01-15 10:30:00":
            return datetime(2024, 1, 15, 10, 30, 0)
        elif date_string == "2024-01-15 12:45:00":
            return datetime(2024, 1, 15, 12, 45, 0)
        else:
            return datetime(2024, 1, 15, 11, 0, 0)


@pytest.fixture
def mock_datetime():
    """Mock datetime module for testing."""
    with patch("chargecollector.datetime") as mock:
        mock.datetime = MockDateTime
        yield mock


@pytest.fixture
def database_rows_empty():
    """Mock empty database query results."""

    def mock_fetchone():
        return None

    def mock_fetchall():
        return []

    return mock_fetchone, mock_fetchall


@pytest.fixture
def database_rows_with_data():
    """Mock database query results with data."""

    def mock_fetchone():
        return (1, datetime.datetime(2024, 1, 15, 10, 0, 0))

    def mock_fetchall():
        return [
            (1, datetime.datetime(2024, 1, 15, 10, 0, 0)),
            (2, datetime.datetime(2024, 1, 15, 11, 0, 0)),
        ]

    return mock_fetchone, mock_fetchall


@pytest.fixture(autouse=True)
def reset_global_state():
    """Reset global state before each test."""
    # This fixture automatically runs before each test
    yield
    # Reset any global state if needed
