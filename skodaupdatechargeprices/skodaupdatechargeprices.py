import asyncio
import datetime
import logging
import os
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

import mariadb
from commons import SLEEPTIME, db_connect, get_logger, load_secret

my_logger = get_logger("skodachargecollect")

my_logger.warning("Starting the application...")


async def read_last_n_lines(filename, n):
    loop = asyncio.get_event_loop()

    def _read():
        with open(filename, "r") as file:
            lines = file.readlines()
            return lines[-n:]

    return await loop.run_in_executor(None, _read)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Start background price updater on app startup
    task = asyncio.create_task(priceupdate())
    try:
        yield
    finally:
        # Ensure background task is cancelled gracefully on shutdown
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(lifespan=_lifespan)


@app.get("/update-charges")
async def update_charges():
    my_logger.debug("Received request to update charges from ")
    await invoke_priceupdate()
    return PlainTextResponse("Charge prices updated.".encode("utf-8"))


async def _count_records_needing_price_updates() -> int:
    """Count how many charge hour records have amounts but no prices."""
    conn, cur = await db_connect(my_logger)
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None,
            cur.execute,
            "SELECT COUNT(*) FROM skoda.charge_hours WHERE price IS NULL AND amount IS NOT NULL",
        )
        count = cur.fetchone()[0]
        return int(count)
    except mariadb.Error as e:
        my_logger.error("Error counting records needing price updates: %s", e)
        return 0


@app.get("/update-all-charges")
async def update_all_charges():
    """Update prices for all records with amount IS NOT NULL and price IS NULL."""
    my_logger.debug("Received request to update ALL outstanding charge prices")

    processed = 0
    max_updates = 5000  # safety bound

    while processed < max_updates:
        remaining = await _count_records_needing_price_updates()
        if remaining == 0:
            break
        # Attempt to update one; if it returns True we made progress
        result = await update_one_charge_price()
        if result:
            processed += 1
            # tiny pause to avoid hammering external APIs and DB
            await asyncio.sleep(0.01)
        else:
            # Could be transient fetch error; brief backoff then re-check remaining
            await asyncio.sleep(0.2)

    leftover = await _count_records_needing_price_updates()
    message = f"Updated {processed} prices. Remaining: {leftover}."
    my_logger.info(message)
    return PlainTextResponse(message.encode("utf-8"))


@app.get("/")
async def root():
    conn, cur = await db_connect(my_logger)
    last_25_lines = await read_last_n_lines("app.log", 15)
    last_25_lines_joined = "".join(last_25_lines)
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, cur.execute, "SELECT COUNT(*) FROM skoda.charge_hours"
        )
        count = cur.fetchone()[0]
        last_25_lines_joined += "\n\nTotal logs in database: %s\n" % count
        await asyncio.get_event_loop().run_in_executor(
            None,
            cur.execute,
            "SELECT * FROM skoda.charge_hours order by log_timestamp desc limit 10",
        )
    except mariadb.Error as e:
        my_logger.error("Error fetching from database: %s", e)
        conn.rollback()
        import signal

        os.kill(os.getpid(), signal.SIGINT)
    rows = cur.fetchall()
    last_25_lines_joined += "\n".join([str(row) for row in rows])
    return PlainTextResponse(last_25_lines_joined.encode("utf-8"))


async def fetch_transport_tariff_from_api(dt: datetime.datetime) -> float:
    """
    Attempt to fetch transport tariff from energidataservice.dk DatahubPricelist API
    for Cerius A/S Nettarif C.

    Returns the tariff rate in DKK/kWh, or raises an exception if not found.
    """
    # Format the datetime for API query
    date_str = dt.strftime("%Y-%m-%d")
    hour = dt.hour

    # Try to find the Cerius A/S Nettarif C tariff
    url = (
        "https://api.energidataservice.dk/dataset/DatahubPricelist"
        '?filter={"ChargeOwner":"Cerius A/S","ChargeType":"D03"}'
        "&sort=ValidFrom DESC&limit=50"
    )

    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    # Look for current Nettarif C tariff (this is a placeholder - the exact
    # charge code may need to be updated when the API includes C-level tariffs)
    for record in data["records"]:
        code = record.get("ChargeTypeCode", "")
        note = record.get("Note", "").lower()
        valid_from = record.get("ValidFrom", "")
        valid_to = record.get("ValidTo")

        # Check if this is a C-level consumption tariff and currently valid
        if (
            "c" in note
            and "nettarif" in note
            and "indfödning" not in note
            and date_str >= valid_from[:10]
            and (valid_to is None or date_str <= valid_to[:10])
        ):

            # Extract hourly price (Price1-Price24, where Price1 = hour 0)
            price_key = f"Price{hour + 1}"
            if price_key in record and record[price_key]:
                return record[price_key] / 1000  # Convert from øre/kWh to DKK/kWh

    # If no API data found, raise exception to trigger fallback
    raise ValueError("Transport tariff not available from API")


async def get_transport_tariff_fallback(dt: datetime.datetime) -> float:
    """
    Fallback transport tariff calculation using current Cerius A/S Nettarif C rates.

    Source: https://stromligning.dk/tariffer/cerius_c
    Last verified: October 2025

    Winter rates (October-March):
    - 00:00-06:00: 13.31 øre/kWh = 0.1331 DKK/kWh
    - 06:00-17:00: 39.92 øre/kWh = 0.3992 DKK/kWh
    - 17:00-21:00: 119.77 øre/kWh = 1.1977 DKK/kWh
    - 21:00-00:00: 39.92 øre/kWh = 0.3992 DKK/kWh

    Summer rates (April-September):
    - 00:00-06:00: 13.31 øre/kWh = 0.1331 DKK/kWh
    - 06:00-17:00: 19.96 øre/kWh = 0.1996 DKK/kWh
    - 17:00-21:00: 51.90 øre/kWh = 0.5190 DKK/kWh
    - 21:00-00:00: 19.96 øre/kWh = 0.1996 DKK/kWh
    """
    month = dt.month
    hour = dt.hour

    if 4 <= month <= 9:  # April to September: summer
        if 0 <= hour < 6:
            return 0.1331
        elif 6 <= hour < 17:
            return 0.1996
        elif 17 <= hour < 21:
            return 0.5190
        else:  # 21:00-00:00
            return 0.1996
    else:  # October to March: winter
        if 0 <= hour < 6:
            return 0.1331
        elif 6 <= hour < 17:
            return 0.3992  # Fixed: was 0.3993, should be 0.3992
        elif 17 <= hour < 21:
            return 1.1977
        else:  # 21:00-00:00
            return 0.3992  # Fixed: was 0.3993, should be 0.3992


async def get_transport_tariff(dt: datetime.datetime) -> float:
    """
    Get transport tariff for the given datetime, preferring API data with fallback.

    Returns the tariff rate in DKK/kWh.
    """
    try:
        # Try to fetch from API first
        return await fetch_transport_tariff_from_api(dt)
    except Exception as e:
        my_logger.debug("API fetch failed, using fallback tariff: %s", e)
        # Fall back to hardcoded rates
        return await get_transport_tariff_fallback(dt)


async def fetch_spot_price(dt: datetime.datetime) -> float:
    hour_utc = dt.strftime("%Y-%m-%dT%H:00:00")
    url = (
        "https://api.energidataservice.dk/dataset/DayAheadPrices"
        "?offset=0&limit=1&sort=TimeUTC%%20DESC"
        '&filter={"PriceArea":"DK2","TimeUTC":"%s"}' % hour_utc
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        # Support tests that mock these as async coroutines
        res = resp.raise_for_status()
        if asyncio.iscoroutine(res):
            await res
        data = resp.json()
        if asyncio.iscoroutine(data):
            data = await data
    if data["records"]:
        dkk_per_mwh = data["records"][0]["DayAheadPriceDKK"]
        dkk_per_kwh = dkk_per_mwh / 1000
        return dkk_per_kwh
    else:
        raise ValueError("No spot price found for %s" % hour_utc)


async def update_one_charge_price():
    conn, cur = await db_connect(my_logger)
    my_logger.debug("Updating one charge price...")
    loop = asyncio.get_event_loop()
    my_logger.debug("Acquiring database connection...")
    await loop.run_in_executor(
        None,
        cur.execute,
        "SELECT id, log_timestamp, amount FROM skoda.charge_hours WHERE price IS NULL AND amount IS NOT NULL ORDER BY log_timestamp ASC LIMIT 1",
    )
    row = cur.fetchone()
    my_logger.debug("Fetched row: %s", row)
    if not row:
        my_logger.info("No records with valid amounts to update.")
        return
    record_id, charge_start, amount = row
    my_logger.debug("Record ID: %s, Charge Start: %s", record_id, charge_start)
    if isinstance(charge_start, str):
        charge_start = datetime.datetime.fromisoformat(charge_start)
    my_logger.debug("Charge Start as datetime: %s", charge_start)
    try:
        my_logger.debug("Fetching spot price...")
        spot_price = await fetch_spot_price(charge_start)
        my_logger.debug("Fetched spot price: %.4f DKK/kWh", spot_price)
    except Exception as e:
        my_logger.error("Failed to fetch spot price: %s", e)
        return
    my_logger.debug("Fetching transport tariff...")
    tariff = await get_transport_tariff(charge_start)
    my_logger.debug("Fetched transport tariff: %.4f DKK/kWh", tariff)
    total_price = (spot_price + tariff) * 1.25
    my_logger.debug("Total price before multiplication: %.4f DKK/kWh", total_price)
    total_price = total_price * amount
    my_logger.debug("Total price calculated: %.4f DKK for %s kWh", total_price, amount)
    my_logger.debug("Updating record in database...")
    try:
        await loop.run_in_executor(
            None,
            cur.execute,
            "UPDATE skoda.charge_hours SET price = %s WHERE id = %s",
            (total_price, record_id),
        )
        conn.commit()
        my_logger.info(
            "Updated record %s with price %.4f DKK/kWh", record_id, total_price
        )
        return True
    except Exception as e:
        my_logger.error("Failed to update record: %s", e)
        conn.rollback()


async def priceupdate():
    my_logger.debug("Starting main function... ")
    while True:
        sleeptime = await invoke_priceupdate()
        my_logger.debug("Sleeping for %s seconds...", sleeptime)
        await asyncio.sleep(sleeptime)


async def invoke_priceupdate():
    my_logger.debug("Invoking price update...")
    updateresult = await update_one_charge_price()
    if updateresult:
        my_logger.debug("Price updated successfully.")
        return 0.011
    else:
        return SLEEPTIME


# Background task is created via FastAPI lifespan; nothing at import time.
