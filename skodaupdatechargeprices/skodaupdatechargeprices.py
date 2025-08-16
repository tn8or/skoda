import asyncio
import datetime
import logging
import os
from contextlib import asynccontextmanager, suppress

import httpx
import mariadb
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

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


async def get_transport_tariff(dt: datetime.datetime) -> float:
    month = dt.month
    hour = dt.hour
    if 4 <= month <= 9:  # April to September: summer
        if 0 <= hour < 6:
            return 0.1331
        elif 6 <= hour < 17:
            return 0.1996
        elif 17 <= hour < 21:
            return 0.5190
        else:
            return 0.1996
    else:  # October to March: winter
        if 0 <= hour < 6:
            return 0.1331
        elif 6 <= hour < 17:
            return 0.3993
        elif 17 <= hour < 21:
            return 1.11977
        else:
            return 0.3993


async def fetch_spot_price(dt: datetime.datetime) -> float:
    hour_utc = dt.strftime("%Y-%m-%dT%H:00Z")
    url = (
        "https://api.energidataservice.dk/dataset/Elspotprices"
        "?offset=0&limit=1&sort=HourUTC%%20DESC"
        '&filter={"PriceArea":"DK2","HourUTC":"%s"}' % hour_utc
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
        eur_per_mwh = data["records"][0]["SpotPriceEUR"]
        dkk_per_kwh = eur_per_mwh * 7.45 / 1000
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
