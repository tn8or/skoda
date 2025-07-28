"""
Basic test suite for chargecollector functionality.

This module contains essential unit tests for core functions.
Memory-optimized for Docker builds.
"""

import asyncio
import os
import sys

# Mock environment variables before importing
import unittest.mock
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

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
    from chargecollector import (
        ChargeCollectorState,
        LocationConfig,
        calculate_and_update_charge_amount,
        create_charge_event,
        find_empty_amount,
        find_next_unlinked_event,
        find_range_from_start,
        find_records_with_no_start_range,
        invoke_charge_collector,
        is_charge_hour_started,
        keep_going_across_hours,
        link_charge_to_event,
        locate_charge_hour,
        process_all_amounts,
        read_last_n_lines,
        start_charge_hour,
        update_charge_with_event_data,
        update_charges_with_event,
    )

    from commons import SLEEPTIME


@pytest.fixture
def mock_db_connect():
    """Mock database connection."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()

    with patch("chargecollector.db_connect") as mock_db:
        mock_db.return_value = (mock_conn, mock_cur)
        yield mock_conn, mock_cur


@pytest.fixture
def mock_mariadb_error():
    """Mock MariaDB error."""
    return mariadb.Error("Test database error")


@pytest.fixture
def sample_charge_hour_row():
    """Sample charge hour row data."""
    return (
        1,  # id
        datetime(2024, 1, 15, 10, 0, 0),  # log_timestamp
        None,  # start_at
        None,  # stop_at
        None,  # amount
        None,  # start_range
        None,  # stop_range
    )


class TestChargeCollectorState:
    """Test cases for ChargeCollectorState dataclass."""

    def test_default_values(self):
        """Test that default values are set correctly."""
        state = ChargeCollectorState()
        assert state.last_hour == ""
        assert state.still_going is False
        assert state.data_processed == 0

    def test_state_modification(self):
        """Test that state can be modified correctly."""
        state = ChargeCollectorState()
        state.last_hour = "2025-01-15 14"
        state.still_going = True
        state.data_processed = 1

        assert state.last_hour == "2025-01-15 14"
        assert state.still_going is True
        assert state.data_processed == 1


class TestLocationConfig:
    """Test cases for LocationConfig dataclass."""

    def test_default_values(self):
        """Test that default home coordinates are set correctly."""
        config = LocationConfig()
        assert config.home_latitude == "55.547"
        assert config.home_longitude == "11.222"


class TestFindNextUnlinkedEvent:
    """Test cases for the find_next_unlinked_event function."""

    @pytest.mark.asyncio
    async def test_finds_unlinked_event(self, mock_db_connect):
        """Test finding an unlinked charge event."""
        mock_conn, mock_cur = mock_db_connect

        # Mock database response
        expected_row = (
            "id1",
            datetime.now(),
            "start",
            100,
            50000,
            "55.5",
            "11.2",
            80,
            None,
        )
        mock_cur.fetchone.return_value = expected_row

        result = await find_next_unlinked_event()

        assert result == expected_row
        mock_cur.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_unlinked_events(self, mock_db_connect):
        """Test when no unlinked events are found."""
        mock_conn, mock_cur = mock_db_connect
        mock_cur.fetchone.return_value = None

        result = await find_next_unlinked_event()

        assert result is None


class TestStartChargeHour:
    """Test the start_charge_hour function."""

    @pytest.mark.asyncio
    async def test_successful_start(self, mock_db_connect):
        """Test successfully starting a charge hour."""
        mock_conn, mock_cur = mock_db_connect

        result = await start_charge_hour("2024-01-15 10", "2024-01-15 10:30:00")

        assert result is True
        mock_cur.execute.assert_called_once_with(
            "UPDATE skoda.charge_hours SET start_at=? WHERE log_timestamp=?",
            ("2024-01-15 10:30:00", "2024-01-15 10:00:00"),
        )
        mock_conn.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_database_error(self, mock_db_connect, mock_mariadb_error):
        """Test handling of database errors."""
        mock_conn, mock_cur = mock_db_connect
        mock_cur.execute.side_effect = mock_mariadb_error

        with pytest.raises(mariadb.Error):
            await start_charge_hour("2024-01-15 10", "2024-01-15 10:30:00")

        mock_conn.rollback.assert_called_once()


class TestIsChargeHourStarted:
    """Test the is_charge_hour_started function."""

    @pytest.mark.asyncio
    async def test_charge_hour_started(self, mock_db_connect, sample_charge_hour_row):
        """Test when charge hour is already started."""
        mock_conn, mock_cur = mock_db_connect
        mock_cur.fetchone.return_value = sample_charge_hour_row

        result = await is_charge_hour_started("2024-01-15 10")

        assert result is True
        mock_cur.execute.assert_called_once_with(
            "SELECT * FROM skoda.charge_hours WHERE log_timestamp = ? "
            "AND start_at IS NOT NULL",
            ("2024-01-15 10:00:00",),
        )

    @pytest.mark.asyncio
    async def test_charge_hour_not_started(self, mock_db_connect):
        """Test when charge hour is not started."""
        mock_conn, mock_cur = mock_db_connect
        mock_cur.fetchone.return_value = None

        result = await is_charge_hour_started("2024-01-15 10")

        assert result is False
        mock_cur.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_database_error(self, mock_db_connect, mock_mariadb_error):
        """Test handling of database errors."""
        mock_conn, mock_cur = mock_db_connect
        mock_cur.execute.side_effect = mock_mariadb_error

        with pytest.raises(mariadb.Error):
            await is_charge_hour_started("2024-01-15 10")

        mock_conn.rollback.assert_called_once()


class TestLocateChargeHour:
    """Test the locate_charge_hour function."""

    @pytest.mark.asyncio
    async def test_existing_charge_hour(self, mock_db_connect, sample_charge_hour_row):
        """Test locating an existing charge hour."""
        mock_conn, mock_cur = mock_db_connect
        mock_cur.fetchone.return_value = sample_charge_hour_row

        result = await locate_charge_hour("2024-01-15 10")

        assert result == 1  # The ID from sample_charge_hour_row
        mock_cur.execute.assert_called_once_with(
            "SELECT * FROM skoda.charge_hours WHERE log_timestamp = ?",
            ("2024-01-15 10:00:00",),
        )

    @pytest.mark.asyncio
    async def test_create_new_charge_hour(self, mock_db_connect):
        """Test creating a new charge hour."""
        mock_conn, mock_cur = mock_db_connect
        mock_cur.fetchone.return_value = None
        mock_cur.lastrowid = 123

        result = await locate_charge_hour("2024-01-15 10")

        assert result == 123
        # Should call SELECT first, then INSERT
        assert mock_cur.execute.call_count == 2
        mock_conn.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_database_error(self, mock_db_connect, mock_mariadb_error):
        """Test handling of database errors."""
        mock_conn, mock_cur = mock_db_connect
        mock_cur.execute.side_effect = mock_mariadb_error

        with pytest.raises(mariadb.Error):
            await locate_charge_hour("2024-01-15 10")

        mock_conn.rollback.assert_called_once()


class TestCalculateAndUpdateChargeAmount:
    """Test cases for the calculate_and_update_charge_amount function."""

    @pytest.mark.asyncio
    async def test_successful_calculation(self, mock_db_connect):
        """Test successful amount calculation."""
        mock_conn, mock_cur = mock_db_connect

        # Mock valid start and stop times (1 hour duration)
        start_time = datetime(2025, 1, 15, 14, 0, 0)
        stop_time = datetime(2025, 1, 15, 15, 0, 0)
        mock_cur.fetchone.return_value = (start_time, stop_time)

        result = await calculate_and_update_charge_amount("test-charge-id")

        # Should return SLEEPTIME (1800) on success
        from commons import SLEEPTIME

        assert result == SLEEPTIME

        # Verify database update was called
        mock_cur.execute.assert_any_call(
            "UPDATE skoda.charge_hours SET amount = ? WHERE id = ?",
            (10.5, "test-charge-id"),
        )
        mock_conn.commit.assert_called()

    @pytest.mark.asyncio
    async def test_no_valid_times(self, mock_db_connect):
        """Test handling when no valid start/stop times are available."""
        mock_conn, mock_cur = mock_db_connect
        mock_cur.fetchone.return_value = (None, None)

        result = await calculate_and_update_charge_amount("test-charge-id")

        # Should return 30 when no valid times
        assert result == 30


class TestFindEmptyAmount:
    """Test cases for find_empty_amount function."""

    @pytest.mark.asyncio
    async def test_finds_empty_amount(self, mock_db_connect):
        """Test finding a charge hour with empty amount."""
        mock_conn, mock_cur = mock_db_connect
        mock_cur.fetchone.return_value = ("test-id",)

        result = await find_empty_amount()

        assert result == "test-id"
        mock_cur.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_empty_amounts(self, mock_db_connect):
        """Test when no empty amounts are found."""
        mock_conn, mock_cur = mock_db_connect
        mock_cur.fetchone.return_value = None

        result = await find_empty_amount()

        assert result is None

    @pytest.mark.asyncio
    async def test_database_error(self, mock_db_connect, mock_mariadb_error):
        """Test handling database errors."""
        mock_conn, mock_cur = mock_db_connect
        mock_cur.execute.side_effect = mock_mariadb_error

        result = await find_empty_amount()

        assert result is None
        mock_conn.rollback.assert_called_once()


class TestCreateChargeEvent:
    """Test cases for create_charge_event function."""

    @pytest.mark.asyncio
    async def test_successful_creation(self, mock_db_connect):
        """Test successful charge event creation."""
        mock_conn, mock_cur = mock_db_connect
        mock_cur.lastrowid = 123  # Mock the return value

        charge_data = (
            "test-id",
            datetime(2025, 1, 15, 14, 30, 0),
            "start",
            100,
            50000,
            "55.547",
            "11.222",
            80,
            None,
        )

        result = await create_charge_event(charge_data)

        assert result == 123  # Should return the lastrowid
        mock_cur.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_database_error(self, mock_db_connect):
        """Test handling database errors."""
        mock_conn, mock_cur = mock_db_connect
        mock_cur.execute.side_effect = mariadb.Error("Test database error")

        charge_data = (
            "test-id",
            datetime(2025, 1, 15, 14, 30, 0),
            "start",
            100,
            50000,
            "55.547",
            "11.222",
            80,
            None,
        )

        with pytest.raises(mariadb.Error, match="Test database error"):
            await create_charge_event(charge_data)

        mock_conn.rollback.assert_called_once()


class TestLinkChargeToEvent:
    """Test cases for link_charge_to_event function."""

    @pytest.mark.asyncio
    async def test_successful_linking(self, mock_db_connect):
        """Test successful charge to event linking."""
        mock_conn, mock_cur = mock_db_connect

        charge_data = (
            "test-id",
            datetime(2025, 1, 15, 14, 30, 0),
            "start",
            100,
            50000,
            "55.547",
            "11.222",
            80,
            None,
        )

        result = await link_charge_to_event(charge_data, "test-charge-id")

        assert result is True
        mock_cur.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_database_error(self, mock_db_connect):
        """Test handling database errors."""
        mock_conn, mock_cur = mock_db_connect
        mock_cur.execute.side_effect = mariadb.Error("Test database error")

        charge_data = (
            "test-id",
            datetime(2025, 1, 15, 14, 30, 0),
            "start",
            100,
            50000,
            "55.547",
            "11.222",
            80,
            None,
        )

        with pytest.raises(mariadb.Error, match="Test database error"):
            await link_charge_to_event(charge_data, "test-charge-id")

        mock_conn.rollback.assert_called_once()


class TestReadLastNLines:
    """Test cases for read_last_n_lines function."""

    def test_read_lines(self, tmp_path):
        """Test reading lines from a file."""
        # Create a temporary file
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3\nline4\nline5\n")

        result = read_last_n_lines(str(test_file), 3)

        # The function returns lines with newlines, so we need to strip them
        expected = ["line3", "line4", "line5"]
        actual = [line.strip() for line in result]
        assert actual == expected

    def test_read_more_lines_than_available(self, tmp_path):
        """Test reading more lines than available in file."""
        # Create a temporary file with only 2 lines
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\n")

        result = read_last_n_lines(str(test_file), 5)

        # The function returns lines with newlines, so we need to strip them
        expected = ["line1", "line2"]
        actual = [line.strip() for line in result]
        assert actual == expected


class TestProcessAllAmounts:
    """Test cases for the process_all_amounts function."""

    @pytest.mark.asyncio
    @patch("chargecollector.find_empty_amount")
    @patch("chargecollector.calculate_and_update_charge_amount")
    async def test_batch_processing_success(self, mock_calculate, mock_find_empty):
        """Test successful batch processing of all empty amounts."""
        from chargecollector import process_all_amounts

        # Mock find_empty_amount to return charge IDs first, then None to stop
        mock_find_empty.side_effect = [
            "charge-id-1",
            "charge-id-2",
            "charge-id-3",
            None,  # Stop the loop
        ]

        # Mock successful amount calculations (SLEEPTIME = 1800 for success)
        mock_calculate.return_value = 1800

        result = await process_all_amounts()

        # Verify the function returned a PlainTextResponse
        assert hasattr(result, "body")
        assert b"Batch processing completed. Processed 3 charge hours" in result.body

        # Verify find_empty_amount was called multiple times
        assert mock_find_empty.call_count == 4

        # Verify calculate_and_update_charge_amount was called for each charge
        assert mock_calculate.call_count == 3
