import asyncio
import datetime as dt
import importlib.util
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure parent directory is in sys.path for import to work
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import skodaupdatechargeprices as m


@pytest.mark.asyncio
async def test_get_transport_tariff_winter_peakhours():
    # Jan 10th, 18:00 -> winter peak
    ts = dt.datetime(2025, 1, 10, 18, 0, 0)
    assert pytest.approx(await m.get_transport_tariff(ts), 0.0001) == 1.1977


@pytest.mark.asyncio
async def test_get_transport_tariff_fallback_values():
    # Test that fallback values match expected Cerius A/S Nettarif C rates

    # Winter rates
    winter_night = dt.datetime(2025, 1, 10, 3, 0, 0)  # 03:00 winter
    winter_day = dt.datetime(2025, 1, 10, 12, 0, 0)  # 12:00 winter
    winter_peak = dt.datetime(2025, 1, 10, 18, 0, 0)  # 18:00 winter
    winter_evening = dt.datetime(2025, 1, 10, 22, 0, 0)  # 22:00 winter

    assert (
        pytest.approx(await m.get_transport_tariff_fallback(winter_night), 0.0001)
        == 0.1331
    )
    assert (
        pytest.approx(await m.get_transport_tariff_fallback(winter_day), 0.0001)
        == 0.3992
    )
    assert (
        pytest.approx(await m.get_transport_tariff_fallback(winter_peak), 0.0001)
        == 1.1977
    )
    assert (
        pytest.approx(await m.get_transport_tariff_fallback(winter_evening), 0.0001)
        == 0.3992
    )

    # Summer rates
    summer_night = dt.datetime(2025, 6, 10, 3, 0, 0)  # 03:00 summer
    summer_day = dt.datetime(2025, 6, 10, 12, 0, 0)  # 12:00 summer
    summer_peak = dt.datetime(2025, 6, 10, 18, 0, 0)  # 18:00 summer
    summer_evening = dt.datetime(2025, 6, 10, 22, 0, 0)  # 22:00 summer

    assert (
        pytest.approx(await m.get_transport_tariff_fallback(summer_night), 0.0001)
        == 0.1331
    )
    assert (
        pytest.approx(await m.get_transport_tariff_fallback(summer_day), 0.0001)
        == 0.1996
    )
    assert (
        pytest.approx(await m.get_transport_tariff_fallback(summer_peak), 0.0001)
        == 0.5190
    )
    assert (
        pytest.approx(await m.get_transport_tariff_fallback(summer_evening), 0.0001)
        == 0.1996
    )


@pytest.mark.asyncio
async def test_fetch_transport_tariff_from_api_fallback_on_failure(monkeypatch):
    # Mock the API to fail, ensure it falls back to hardcoded values
    async def mock_api_failure(*args, **kwargs):
        raise ValueError("API not available")

    monkeypatch.setattr(
        "skodaupdatechargeprices.fetch_transport_tariff_from_api", mock_api_failure
    )

    # Should fall back to hardcoded value for winter peak
    winter_peak = dt.datetime(2025, 1, 10, 18, 0, 0)
    result = await m.get_transport_tariff(winter_peak)
    assert pytest.approx(result, 0.0001) == 1.1977


@pytest.mark.asyncio
async def test_fetch_spot_price_new_api():
    """Test the new DayAheadPrices API for dates >= 2025-10-01"""
    fake_data = {"records": [{"DayAheadPriceDKK": 1000.0}]}
    with patch("skodaupdatechargeprices.httpx.AsyncClient") as C:
        client = C.return_value.__aenter__.return_value
        client.get = AsyncMock()
        client.get.return_value.json.return_value = fake_data
        client.get.return_value.raise_for_status.return_value = None

        # Test date after cutover (should use new API)
        price = await m.fetch_spot_price(dt.datetime(2025, 10, 5, 12, 0, 0))

        # Verify it called the API with DayAheadPrices endpoint
        client.get.assert_called_once()
        call_args = client.get.call_args[0][0]
        assert "DayAheadPrices" in call_args
        assert "2025-10-05T12:00:00" in call_args

    # 1000 DKK/MWh -> 1.0 DKK/kWh
    assert pytest.approx(price, 0.0001) == 1.0


@pytest.mark.asyncio
async def test_fetch_spot_price_legacy_api():
    """Test the legacy Elspotprices API for dates < 2025-10-01"""
    fake_data = {"records": [{"SpotPriceEUR": 100.0}]}
    with patch("skodaupdatechargeprices.httpx.AsyncClient") as C:
        client = C.return_value.__aenter__.return_value
        client.get = AsyncMock()
        client.get.return_value.json.return_value = fake_data
        client.get.return_value.raise_for_status.return_value = None

        # Test date before cutover (should use legacy API)
        price = await m.fetch_spot_price(dt.datetime(2025, 9, 15, 12, 0, 0))

        # Verify it called the API with Elspotprices endpoint
        client.get.assert_called_once()
        call_args = client.get.call_args[0][0]
        assert "Elspotprices" in call_args
        assert "2025-09-15T12:00Z" in call_args

    # 100 EUR/MWh * 7.45 / 1000 = 0.745 DKK/kWh
    assert pytest.approx(price, 0.0001) == 0.745


@pytest.mark.asyncio
async def test_fetch_spot_price_api_selection():
    """Test that the correct API is selected based on date"""
    # Mock both APIs
    with patch("skodaupdatechargeprices.fetch_spot_price_legacy") as mock_legacy, patch(
        "skodaupdatechargeprices.fetch_spot_price_new"
    ) as mock_new:

        mock_legacy.return_value = 0.5
        mock_new.return_value = 0.6

        # Test date before cutover
        result1 = await m.fetch_spot_price(dt.datetime(2025, 9, 30, 12, 0, 0))
        mock_legacy.assert_called_once()
        mock_new.assert_not_called()
        assert result1 == 0.5

        # Reset mocks
        mock_legacy.reset_mock()
        mock_new.reset_mock()

        # Test date after cutover
        result2 = await m.fetch_spot_price(dt.datetime(2025, 10, 1, 12, 0, 0))
        mock_new.assert_called_once()
        mock_legacy.assert_not_called()
        assert result2 == 0.6


@pytest.mark.asyncio
async def test_fetch_spot_price_fallback_mechanism():
    """Test that fallback works when primary API fails"""
    # Test legacy API failure, fallback to new API
    with patch("skodaupdatechargeprices.fetch_spot_price_legacy") as mock_legacy, patch(
        "skodaupdatechargeprices.fetch_spot_price_new"
    ) as mock_new:

        mock_legacy.side_effect = Exception("Legacy API failed")
        mock_new.return_value = 0.8

        result = await m.fetch_spot_price(dt.datetime(2025, 9, 15, 12, 0, 0))
        mock_legacy.assert_called_once()
        mock_new.assert_called_once()  # Called as fallback
        assert result == 0.8

    # Test new API failure, fallback to legacy API (separate mock context)
    with patch(
        "skodaupdatechargeprices.fetch_spot_price_legacy"
    ) as mock_legacy2, patch(
        "skodaupdatechargeprices.fetch_spot_price_new"
    ) as mock_new2:

        mock_new2.side_effect = Exception("New API failed")
        mock_legacy2.return_value = 0.7

        result = await m.fetch_spot_price(dt.datetime(2025, 10, 5, 12, 0, 0))
        mock_new2.assert_called_once()
        mock_legacy2.assert_called_once()  # Called as fallback
        assert result == 0.7


@pytest.mark.asyncio
async def test_fetch_spot_price_both_apis_fail():
    """Test that appropriate error is raised when both APIs fail"""
    with patch("skodaupdatechargeprices.fetch_spot_price_legacy") as mock_legacy, patch(
        "skodaupdatechargeprices.fetch_spot_price_new"
    ) as mock_new:

        mock_legacy.side_effect = Exception("Legacy failed")
        mock_new.side_effect = Exception("New failed")

        with pytest.raises(ValueError, match="Both APIs failed"):
            await m.fetch_spot_price(dt.datetime(2025, 9, 15, 12, 0, 0))


@pytest.mark.asyncio
async def test_update_one_charge_price_happy_path(monkeypatch):
    # Mock DB connect
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = (123, dt.datetime(2025, 6, 3, 12, 0, 0), 2.0)
    monkeypatch.setattr(
        "skodaupdatechargeprices.db_connect", AsyncMock(return_value=(conn, cur))
    )

    # Mock fetch_spot_price and tariff
    monkeypatch.setattr(
        "skodaupdatechargeprices.fetch_spot_price", AsyncMock(return_value=1.0)
    )
    monkeypatch.setattr(
        "skodaupdatechargeprices.get_transport_tariff", AsyncMock(return_value=0.5)
    )

    # Run
    result = await m.update_one_charge_price()

    # Verify DB update was attempted and commit happened
    assert result is True
    assert cur.execute.called
    assert conn.commit.called


@pytest.mark.asyncio
async def test_update_one_charge_price_no_row(monkeypatch):
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = None
    monkeypatch.setattr(
        "skodaupdatechargeprices.db_connect", AsyncMock(return_value=(conn, cur))
    )

    result = await m.update_one_charge_price()
    assert result is None
    assert not conn.commit.called
