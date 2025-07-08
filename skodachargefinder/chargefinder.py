import asyncio
import datetime
import json
import logging
import os
import time

import mariadb
from commons import load_secret
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import PlainTextResponse

my_logger = logging.getLogger("skodachargefindlogger")
my_logger.setLevel(logging.DEBUG)

file_handler = logging.FileHandler("app.log")
file_handler.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)

# Optional: set a formatter
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# Add the handler to the logger
my_logger.addHandler(file_handler)
my_logger.addHandler(console_handler)

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
            "SELECT * FROM skoda.charge_events ORDER BY event_timestamp DESC LIMIT 1"
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
            "INSERT INTO skoda.charge_events (event_timestamp, pos_lat, pos_lon, charged_range, mileage, event_type, soc) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                charge["timestamp"],
                charge["pos_lat"],
                charge["pos_lon"],
                charge["charged_range"],
                charge["mileage"],
                charge["event_type"],
                charge["soc"],
            ),
        )
        conn.commit()
        my_logger.debug("Charge written to database successfully.")
    except mariadb.Error as e:
        my_logger.error(f"Error writing charge to database: {e}")
        conn.rollback()


async def find_vehicle_mileage(hour):
    my_logger.debug(f"Finding vehicle mileage for {hour}:00")
    try:
        cur.execute(
            "SELECT log_message FROM skoda.rawlogs WHERE log_timestamp >= ? AND log_timestamp < ? AND log_message LIKE '%mileage:%' ORDER BY log_timestamp DESC LIMIT 1",
            (
                f"{hour}:00:00",
                f"{hour}:59:59",
            ),
        )
        row = cur.fetchone()
        if row:
            my_logger.debug("Vehicle mileage found: %s", row)
            mileage = (
                row[0].split(":")[1].strip()
            )  # Assuming mileage is in the third column
            my_logger.debug("Returning mileage %s", mileage)
            return mileage
        else:
            my_logger.debug("No vehicle mileage found.")
            return None
    except mariadb.Error as e:
        my_logger.error(f"Error fetching vehicle mileage: {e}")
        conn.rollback()
        return None


async def find_vehicle_position(hour):
    my_logger.debug(f"Finding vehicle position for {hour}:00")
    try:
        cur.execute(
            "SELECT log_message FROM skoda.rawlogs WHERE log_timestamp >= ? AND log_timestamp < ? AND log_message LIKE 'Vehicle positions%' ORDER BY log_timestamp DESC LIMIT 1",
            (
                f"{hour}:00:00",
                f"{hour}:59:59",
            ),
        )
        row = cur.fetchone()
        if row:
            my_logger.debug("Vehicle position found: %s", row)
            positionarray = row[0].split(":")
            my_logger.debug("Found position %s", positionarray)
            lat = positionarray[2].strip()
            lat = lat.replace(", lng", "")
            lon = positionarray[3].strip()
            position = []
            position.append(lat)
            position.append(lon)
            my_logger.debug("Returning position %s", position)
            return position  # Assuming the position is in the third column
        else:
            my_logger.debug("No vehicle position found.")
            return None
    except mariadb.Error as e:
        my_logger.error(f"Error fetching vehicle position: {e}")
        conn.rollback()
        return None


async def fetch_and_store_charge():
    my_logger.debug("Fetching and storing charge...")
    last_stored_charge = await read_last_charge()
    if last_stored_charge:
        last_timestamp = last_stored_charge[1]
        my_logger.debug(f"Last stored charge timestamp: {last_timestamp}")
    else:
        last_timestamp = 0

    # see if newer charges are available in the rawlogs table
    query = (
        "SELECT * FROM skoda.rawlogs WHERE log_timestamp > ? and "
        "(log_message like '%ChargingState.CHARGING%' or log_message like '%ChargingState.READY_FOR_CHARGING%') "
        "order by log_timestamp ASC"
    )
    my_logger.debug(f"Executing query: {query} with last_timestamp: {last_timestamp}")
    cur.execute(query, (last_timestamp,))
    new_charge_row = cur.fetchone()
    if not new_charge_row:
        my_logger.debug("No new charge found in rawlogs table.")
        return 600
    my_logger.debug(f"Charge row fetched: {new_charge_row}")

    dt_str = new_charge_row[0].strftime("%Y-%m-%d %H:%M:%S")
    my_logger.debug(f"Charge timestamp: {dt_str}")
    dt_str_split = dt_str.split(":")
    position = await find_vehicle_position(dt_str_split[0])
    my_logger.debug(f"Vehicle position found: {position}")
    mileage = await find_vehicle_mileage(dt_str_split[0])
    my_logger.debug(f"Vehicle mileage found: {mileage}")

    charged_range = new_charge_row[1].split("charged_range=")[1].split(",")[0]
    if "READY_FOR_CHARGING" in new_charge_row[1]:
        operation = "stop"
        my_logger.debug("Charge operation is 'stop'")
    elif "CHARGING" in new_charge_row[1]:
        operation = "start"
        my_logger.debug("Charge operation is 'start'")
    else:
        my_logger.error("No charge operation foudn in row")
        await time.sleep(10)

    new_charge = {
        "timestamp": dt_str,
        "pos_lat": position[0] if position else None,
        "pos_lon": position[1] if position else None,
        "charged_range": charged_range,
        "mileage": mileage,
        "event_type": operation,
        "soc": new_charge_row[1].split("soc=")[1].split(",")[0],
    }

    my_logger.debug(f"New charge fetched: {new_charge}")
    if last_timestamp is 0 or new_charge["timestamp"] != last_timestamp:
        my_logger.debug(
            "New charge is newer than the last stored charge, writing to DB..."
        )
        await write_charge_to_db(new_charge)
        # quick turnaround since theres still records in the database
        return 0.001
    else:
        my_logger.debug("No new charge to write, skipping...")
        # there were no new records, sleep for a while before checking again


async def chargerunner():
    my_logger.debug("Starting main function...")

    while True:
        my_logger.debug("Running chargerunner...")
        sleeptime = await fetch_and_store_charge()
        # Sleep for 10 seconds before the next iteration
        await asyncio.sleep(sleeptime if sleeptime else 10)


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
        cur.execute("SELECT COUNT(*) FROM skoda.charge_events")
        count = cur.fetchone()[0]
        last_25_lines_joined += f"\n\nTotal logs in database: {count}\n"
        cur.execute(
            "SELECT * FROM skoda.charge_events order by event_timestamp desc limit 10"
        )
    except mariadb.Error as e:
        my_logger.error(f"Error fetching from database: {e}")
        conn.rollback()
        import os
        import signal

        os.kill(os.getpid(), signal.SIGINT)
    rows = cur.fetchall()
    last_25_lines_joined += "\n".join([str(row) for row in rows])

    return PlainTextResponse(last_25_lines_joined.encode("utf-8"))


background = asyncio.create_task(chargerunner())
