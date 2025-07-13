import asyncio
import datetime
import logging
import os

import httpx
import mariadb
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from commons import get_logger, load_secret

my_logger = get_logger("skodachargecollect")

my_logger.warning("Starting the application...")

try:
    my_logger.debug("Connecting to MariaDB...")
    conn = mariadb.connect(
        user=load_secret("MARIADB_USERNAME"),
        password=load_secret("MARIADB_PASSWORD"),
        host=load_secret("MARIADB_HOSTNAME"),
        port=3306,
        database=load_secret("MARIADB_DATABASE"),
    )
    conn.auto_reconnect = True
    my_logger.debug("Connected to MariaDB")

except mariadb.Error as e:
    my_logger.error("Error connecting to MariaDB Platform: %s", e)
    print(f"Error connecting to MariaDB Platform: {e}")
    import signal

    os.kill(os.getpid(), signal.SIGINT)

cur = conn.cursor()


async def read_last_n_lines(filename, n):
    loop = asyncio.get_event_loop()

    def _read():
        with open(filename, "r") as file:
            lines = file.readlines()
            return lines[-n:]

    return await loop.run_in_executor(None, _read)


app = FastAPI()


@app.get("/")
async def root():
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
        resp.raise_for_status()
        data = resp.json()
    if data["records"]:
        eur_per_mwh = data["records"][0]["SpotPriceEUR"]
        dkk_per_kwh = eur_per_mwh * 7.45 / 1000
        return dkk_per_kwh
    else:
        raise ValueError("No spot price found for %s" % hour_utc)


async def update_one_charge_price():
    my_logger.debug("Updating one charge price...")
    loop = asyncio.get_event_loop()
    my_logger.debug("Acquiring database connection...")
    await loop.run_in_executor(
        None,
        cur.execute,
        "SELECT id, log_timestamp, amount FROM skoda.charge_hours WHERE price IS NULL ORDER BY log_timestamp ASC LIMIT 1",
    )
    row = cur.fetchone()
    my_logger.debug("Fetched row: %s", row)
    if not row:
        my_logger.info("No records to update.")
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
        my_logger.debug("Running main loop...")
        if await update_one_charge_price():
            my_logger.debug("Price updated successfully.")
            await asyncio.sleep(5)
        else:
            await asyncio.sleep(600)


background = asyncio.create_task(priceupdate())
