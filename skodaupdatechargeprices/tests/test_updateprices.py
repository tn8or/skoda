import asyncio
import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import skodaupdatechargeprices.skodaupdatechargeprices as m


@pytest.mark.asyncio
async def test_get_transport_tariff_winter_peakhours():
    # Jan 10th, 18:00 -> winter peak
    ts = dt.datetime(2025, 1, 10, 18, 0, 0)
    assert pytest.approx(await m.get_transport_tariff(ts), 0.0001) == 1.11977


@pytest.mark.asyncio
async def test_fetch_spot_price_parses_response():
    fake_data = {"records": [{"SpotPriceEUR": 1000.0}]}
    with patch(
        "skodaupdatechargeprices.skodaupdatechargeprices.httpx.AsyncClient"
    ) as C:
        client = C.return_value.__aenter__.return_value
        client.get = AsyncMock()
        client.get.return_value.json.return_value = fake_data
        client.get.return_value.raise_for_status.return_value = None
        price = await m.fetch_spot_price(dt.datetime(2025, 6, 3, 12, 0, 0))
    # 1000 EUR/MWh -> 7.45 DKK/kWh
    assert pytest.approx(price, 0.0001) == 7.45


@pytest.mark.asyncio
async def test_update_one_charge_price_happy_path(monkeypatch):
    # Mock DB connect
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = (123, dt.datetime(2025, 6, 3, 12, 0, 0), 2.0)
    monkeypatch.setattr(m, "db_connect", AsyncMock(return_value=(conn, cur)))

    # Mock fetch_spot_price and tariff
    monkeypatch.setattr(m, "fetch_spot_price", AsyncMock(return_value=1.0))
    monkeypatch.setattr(m, "get_transport_tariff", AsyncMock(return_value=0.5))

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
    monkeypatch.setattr(m, "db_connect", AsyncMock(return_value=(conn, cur)))

    result = await m.update_one_charge_price()
    assert result is None
    assert not conn.commit.called
