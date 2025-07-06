import asyncio
import logging
import os
import time

import mariadb
from commons import load_secret
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import PlainTextResponse

my_logger = logging.getLogger("skodaimportlogger")
my_logger.setLevel(logging.DEBUG)

file_handler = logging.FileHandler("app.log")
file_handler.setLevel(logging.DEBUG)

# Optional: set a formatter
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)

# Add the handler to the logger
my_logger.addHandler(file_handler)

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
    my_logger.error(f"Error connecting to MariaDB Platform: {e}")
    print(f"Error connecting to MariaDB Platform: {e}")
    import os
    import signal

    os.kill(os.getpid(), signal.SIGINT)

cur = conn.cursor()


async def read_last_charge():
    try:
        my_logger.debug("Fetching last charge from database...")
        cur.execute(
            "SELECT * FROM skoda.charge_hours ORDER BY log_timestamp DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row:
            my_logger.debug(f"Last charge found: {row}")
            return row
        else:
            my_logger.debug("No charges found in the database.")
            return None
    except mariadb.Error as e:
        my_logger.error(f"Error fetching last charge: {e}")
        conn.rollback()
        return None


async def write_charge_to_db(charge):
    try:
        my_logger.debug(f"Writing charge to database: {charge}")
        cur.execute(
            "INSERT INTO skoda.charge_hours (log_timestamp, amount, position) VALUES (?, ?, ?)",
            (charge["timestamp"], charge["amount"], charge["position"]),
        )
        conn.commit()
        my_logger.debug("Charge written to database successfully.")
    except mariadb.Error as e:
        my_logger.error(f"Error writing charge to database: {e}")
        conn.rollback()


async def fetch_and_store_charge():
    my_logger.debug("Fetching and storing charge...")
    last_stored_charge = await read_last_charge()
    if last_stored_charge:
        last_timestamp = last_stored_charge[0]
        my_logger.debug(f"Last stored charge timestamp: {last_timestamp}")
    else:
        last_timestamp = None

    # see if newer charges are available in the rawlogs table
    query = "SELECT * FROM skoda.rawlogs WHERE log_timestamp > ? ORDER BY log_timestamp DESC LIMIT 1"
    my_logger.debug(f"Executing query: {query} with last_timestamp: {last_timestamp}")
    cur.execute(query, (last_timestamp,))
    new_charge_row = cur.fetchone()
    if not new_charge_row:
        my_logger.debug("No new charge found in rawlogs table.")
        return
    new_charge = {
        "timestamp": new_charge_row[0],
        "amount": new_charge_row[1],
        "position": new_charge_row[2],

    my_logger.debug(f"New charge fetched: {new_charge}")
    if last_timestamp is None or new_charge["timestamp"] > last_timestamp:
        my_logger.debug(
            "New charge is newer than the last stored charge, writing to DB..."
        )
        await write_charge_to_db(new_charge)
    else:
        my_logger.debug("No new charge to write, skipping...")


async def chargerunner():
    my_logger.debug("Starting main function...")

    while True:
        my_logger.debug("Running chargerunner...")
        # Sleep for 10 seconds before the next iteration
        await asyncio.sleep(5)


def read_last_n_lines(filename, n):
    with open(filename, "r") as file:
        lines = file.readlines()
        return lines[-n:]


app = FastAPI()


@app.get("/")
async def root():
    last_25_lines = read_last_n_lines("app.log", 15)
    last_25_lines_joined = "".join(last_25_lines)
    try:
        cur.execute("SELECT COUNT(*) FROM skoda.charge_hours")
        count = cur.fetchone()[0]
        last_25_lines_joined += f"\n\nTotal logs in database: {count}\n"
        cur.execute(
            "SELECT * FROM skoda.charge_hours order by log_timestamp desc limit 10"
        )
    except mariadb.Error as e:
        my_logger.error(f"Error fetching from database: {e}")
        conn.rollback()
        import os
        import signal

        os.kill(os.getpid(), signal.SIGINT)
    for log_timestamp, log_message in cur:
        last_25_lines_joined += f"{log_timestamp} - {log_message}\n"

    return PlainTextResponse(last_25_lines_joined.encode("utf-8"))


background = asyncio.create_task(chargerunner())
