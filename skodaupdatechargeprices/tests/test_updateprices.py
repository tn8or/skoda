import asyncio
import datetime as dt
import importlib.util
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Load the module directly from the .py file
spec = importlib.util.spec_from_file_location(
    "skodaupdatechargeprices",
    os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "skodaupdatechargeprices.py"
    ),
)
m = importlib.util.module_from_spec(spec)
sys.modules["skodaupdatechargeprices"] = m
spec.loader.exec_module(m)


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
    winter_day = dt.datetime(2025, 1, 10, 12, 0, 0)   # 12:00 winter  
    winter_peak = dt.datetime(2025, 1, 10, 18, 0, 0)  # 18:00 winter
    winter_evening = dt.datetime(2025, 1, 10, 22, 0, 0) # 22:00 winter
    
    assert pytest.approx(await m.get_transport_tariff_fallback(winter_night), 0.0001) == 0.1331
    assert pytest.approx(await m.get_transport_tariff_fallback(winter_day), 0.0001) == 0.3992
    assert pytest.approx(await m.get_transport_tariff_fallback(winter_peak), 0.0001) == 1.1977
    assert pytest.approx(await m.get_transport_tariff_fallback(winter_evening), 0.0001) == 0.3992
    
    # Summer rates
    summer_night = dt.datetime(2025, 6, 10, 3, 0, 0)   # 03:00 summer
    summer_day = dt.datetime(2025, 6, 10, 12, 0, 0)    # 12:00 summer
    summer_peak = dt.datetime(2025, 6, 10, 18, 0, 0)   # 18:00 summer
    summer_evening = dt.datetime(2025, 6, 10, 22, 0, 0) # 22:00 summer
    
    assert pytest.approx(await m.get_transport_tariff_fallback(summer_night), 0.0001) == 0.1331
    assert pytest.approx(await m.get_transport_tariff_fallback(summer_day), 0.0001) == 0.1996
    assert pytest.approx(await m.get_transport_tariff_fallback(summer_peak), 0.0001) == 0.5190
    assert pytest.approx(await m.get_transport_tariff_fallback(summer_evening), 0.0001) == 0.1996


@pytest.mark.asyncio
async def test_fetch_transport_tariff_from_api_fallback_on_failure(monkeypatch):
    # Mock the API to fail, ensure it falls back to hardcoded values
    async def mock_api_failure(*args, **kwargs):
        raise ValueError("API not available")
    
    monkeypatch.setattr("skodaupdatechargeprices.fetch_transport_tariff_from_api", mock_api_failure)
    
    # Should fall back to hardcoded value for winter peak
    winter_peak = dt.datetime(2025, 1, 10, 18, 0, 0)
    result = await m.get_transport_tariff(winter_peak)
    assert pytest.approx(result, 0.0001) == 1.1977


@pytest.mark.asyncio
async def test_fetch_spot_price_parses_response():
    fake_data = {"records": [{"DayAheadPriceDKK": 1000.0}]}
    with patch("skodaupdatechargeprices.httpx.AsyncClient") as C:
        client = C.return_value.__aenter__.return_value
        client.get = AsyncMock()
        client.get.return_value.json.return_value = fake_data
        client.get.return_value.raise_for_status.return_value = None
        price = await m.fetch_spot_price(dt.datetime(2025, 6, 3, 12, 0, 0))
    # 1000 DKK/MWh -> 1.0 DKK/kWh
    assert pytest.approx(price, 0.0001) == 1.0


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
