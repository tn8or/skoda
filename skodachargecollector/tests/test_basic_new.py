"""
Additional tests for chargecollector power-based amount calculation and fixes.

Covers:
- Power-based integration from rawlogs
- Heuristic fallback when no power readings available
- SoC verification logging path
- Fixing negative amounts and clearing negative prices
"""

import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _env_patch():
    """Set minimal env to allow module import and logging."""
    with patch.dict(
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
        clear=False,
    ):
        yield


def _make_db_mocks():
    """Create mock DB connection and cursor."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    return mock_conn, mock_cur


@pytest.mark.asyncio
async def test_amount_from_power_readings_overrides_heuristic():
    """Power-based integration should set amount != duration*10.5 when readings exist."""
    # Arrange
    start_time = datetime(2025, 1, 15, 14, 0, 0)
    stop_time = datetime(2025, 1, 15, 15, 0, 0)
    conn, cur = _make_db_mocks()

    def exec_side_effect(sql, params=None):
        # First query: start/stop times for the hour
        if sql.startswith("SELECT start_at, stop_at FROM skoda.charge_hours"):
            cur.fetchone.return_value = (start_time, stop_time)
        # Before reading at/before start: power 5 kW
        elif (
            "FROM skoda.rawlogs" in sql
            and "charge_power_in_kw" in sql
            and "log_timestamp <=" in sql
            and "LIMIT 1" in sql
        ):
            msg = "Charging data fetched: charge_power_in_kw=5"
            cur.fetchone.return_value = (start_time - timedelta(seconds=10), msg)
        # Within interval: one reading at +30 min with power 15 kW
        elif (
            "FROM skoda.rawlogs" in sql
            and "charge_power_in_kw" in sql
            and "log_timestamp >" in sql
            and "log_timestamp <=" in sql
        ):
            msg2 = "Charging data fetched: charge_power_in_kw=15"
            cur.fetchall.return_value = [(start_time + timedelta(minutes=30), msg2)]
        else:
            # Update or other selects
            cur.fetchone.return_value = None
            cur.fetchall.return_value = []

    cur.execute.side_effect = exec_side_effect

    with patch("chargecollector.db_connect", return_value=(conn, cur)):
        from chargecollector import calculate_and_update_charge_amount

        # Act
        result = await calculate_and_update_charge_amount("cid-1")

    # Assert
    # Expect energy = 0.5h*5kW + 0.5h*15kW = 10.0 (not 10.5)
    update_calls = [
        c
        for c in cur.execute.call_args_list
        if str(c.args[0]).startswith("UPDATE skoda.charge_hours SET amount")
    ]
    assert update_calls, "Expected an UPDATE amount call"
    _, kwargs = update_calls[0]
    # args format: (sql, (amount, charge_id))
    amount_set = update_calls[0].args[1][0]
    assert pytest.approx(amount_set, rel=1e-6) == 10.0
    assert result is not None


@pytest.mark.asyncio
async def test_amount_fallbacks_to_heuristic_when_no_power_readings():
    """When no power readings are found, fallback to duration*10.5."""
    start_time = datetime(2025, 1, 15, 14, 0, 0)
    stop_time = datetime(2025, 1, 15, 15, 0, 0)
    conn, cur = _make_db_mocks()

    def exec_side_effect(sql, params=None):
        if sql.startswith("SELECT start_at, stop_at FROM skoda.charge_hours"):
            cur.fetchone.return_value = (start_time, stop_time)
        elif "FROM skoda.rawlogs" in sql:
            # No readings available
            if "LIMIT 1" in sql:
                cur.fetchone.return_value = None
            else:
                cur.fetchall.return_value = []
        else:
            cur.fetchone.return_value = None
            cur.fetchall.return_value = []

    cur.execute.side_effect = exec_side_effect

    with patch("chargecollector.db_connect", return_value=(conn, cur)):
        from chargecollector import calculate_and_update_charge_amount

        result = await calculate_and_update_charge_amount("cid-2")

    update_calls = [
        c
        for c in cur.execute.call_args_list
        if str(c.args[0]).startswith("UPDATE skoda.charge_hours SET amount")
    ]
    assert update_calls, "Expected an UPDATE amount call"
    amount_set = update_calls[0].args[1][0]
    assert pytest.approx(amount_set, rel=1e-6) == 10.5
    assert result is not None


@pytest.mark.asyncio
async def test_soc_verification_logs_when_capacity_set():
    """Setting SKODA_BATTERY_CAPACITY_KWH should trigger SoC verification log."""
    start_time = datetime(2025, 1, 15, 14, 0, 0)
    stop_time = datetime(2025, 1, 15, 15, 0, 0)
    conn, cur = _make_db_mocks()

    def exec_side_effect(sql, params=None):
        if sql.startswith("SELECT start_at, stop_at FROM skoda.charge_hours"):
            cur.fetchone.return_value = (start_time, stop_time)
        elif (
            "FROM skoda.rawlogs" in sql
            and "charge_power_in_kw" in sql
            and "LIMIT 1" in sql
        ):
            msg = "Charging data fetched: charge_power_in_kw=5"
            cur.fetchone.return_value = (start_time - timedelta(seconds=1), msg)
        elif (
            "FROM skoda.rawlogs" in sql
            and "charge_power_in_kw" in sql
            and "log_timestamp >" in sql
        ):
            msg2 = "Charging data fetched: charge_power_in_kw=15"
            cur.fetchall.return_value = [(start_time + timedelta(minutes=30), msg2)]
        elif (
            "state_of_charge_in_percent" in sql
            and "log_timestamp <=" in sql
            and "LIMIT 1" in sql
        ):
            # First call will be for start_time, then stop_time; return different SoC values
            prev = getattr(cur, "_soc_calls", 0)
            if prev == 0:
                cur.fetchone.return_value = (
                    start_time,
                    "Charging data fetched: state_of_charge_in_percent=30",
                )
            else:
                cur.fetchone.return_value = (
                    stop_time,
                    "Charging data fetched: state_of_charge_in_percent=41",
                )
            cur._soc_calls = prev + 1
        else:
            cur.fetchone.return_value = None
            cur.fetchall.return_value = []

    cur.execute.side_effect = exec_side_effect

    with patch("chargecollector.db_connect", return_value=(conn, cur)), patch.dict(
        os.environ, {"SKODA_BATTERY_CAPACITY_KWH": "82"}, clear=False
    ), patch("chargecollector.my_logger.info") as info_log:
        from chargecollector import calculate_and_update_charge_amount

        await calculate_and_update_charge_amount("cid-3")

    # Expect an info log containing the SoC verification message
    assert any("SoC verification:" in str(c.args[0]) for c in info_log.call_args_list)


@pytest.mark.asyncio
async def test_fix_negative_amounts_recalculates_and_clears_negative_prices():
    """Negative amounts should be recalculated and negative prices set to NULL."""
    start_time = datetime(2025, 1, 15, 14, 0, 0)
    stop_time = datetime(2025, 1, 15, 15, 0, 0)
    conn, cur = _make_db_mocks()

    # Sequence: select negatives (amounts), then negative prices
    def exec_side_effect(sql, params=None):
        if sql.startswith(
            "SELECT id, start_at, stop_at, amount FROM skoda.charge_hours WHERE amount < 0"
        ):
            cur.fetchall.return_value = [("neg-1", start_time, stop_time, -1.0)]
        elif sql.startswith("SELECT id, price FROM skoda.charge_hours WHERE price < 0"):
            cur.fetchall.return_value = [("neg-1", -0.1)]
        elif sql.startswith("UPDATE skoda.charge_hours SET amount = "):
            # Nothing to do; captured via call_args_list
            pass
        elif sql.startswith("UPDATE skoda.charge_hours SET price = NULL"):
            pass
        else:
            # Rawlogs used by power integration
            if (
                "FROM skoda.rawlogs" in sql
                and "charge_power_in_kw" in sql
                and "log_timestamp <=" in sql
                and "LIMIT 1" in sql
            ):
                msg = "Charging data fetched: charge_power_in_kw=7"
                cur.fetchone.return_value = (start_time - timedelta(seconds=5), msg)
            elif (
                "FROM skoda.rawlogs" in sql
                and "charge_power_in_kw" in sql
                and "log_timestamp >" in sql
            ):
                msg2 = "Charging data fetched: charge_power_in_kw=13"
                cur.fetchall.return_value = [
                    (start_time + timedelta(minutes=20), msg2),
                    (
                        start_time + timedelta(minutes=40),
                        "Charging data fetched: charge_power_in_kw=11",
                    ),
                ]
            else:
                cur.fetchone.return_value = None
                cur.fetchall.return_value = []

    cur.execute.side_effect = exec_side_effect

    with patch("chargecollector.db_connect", return_value=(conn, cur)), patch(
        "chargecollector.pull_api", new=AsyncMock()
    ):
        from chargecollector import fix_negative_amounts

        msg = await fix_negative_amounts()

    # Assert that amount was updated and price nullified
    update_amount_calls = [
        c
        for c in cur.execute.call_args_list
        if str(c.args[0]).startswith("UPDATE skoda.charge_hours SET amount")
    ]
    assert update_amount_calls, "Expected amount update in fixer"
    update_price_null_calls = [
        c
        for c in cur.execute.call_args_list
        if str(c.args[0]).startswith("UPDATE skoda.charge_hours SET price = NULL")
    ]
    assert update_price_null_calls, "Expected price NULL update in fixer"
    assert "amounts fixed" in msg
