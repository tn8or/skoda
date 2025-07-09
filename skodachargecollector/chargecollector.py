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

HOME_LATITUDE = "55.547"
HOME_LONGITUDE = "11.222"
lasthour = ""
stillgoing = False

my_logger = logging.getLogger("skodachargecollect")
my_logger.setLevel(logging.DEBUG)

file_handler = logging.FileHandler("app.log")
file_handler.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)

# Optional: set a formatter
formatter = logging.Formatter("%(funcName)s - %(lineno)d - %(message)s")
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


async def update_charges_with_event(charge):
    my_logger.debug("Updating charges with event data from row %s", charge)
    hour = charge[1].strftime("%Y-%m-%d %H")
    my_logger.debug("Locating charge hour for %s", hour)
    event_id = await locate_charge_hour(hour)
    my_logger.debug("Charge hour located: %s", event_id)
    if await update_charge_with_event_data(event_id, charge):
        my_logger.debug("Charge updated with event data successfully.")
        await link_charge_to_event(charge, event_id)
    else:
        my_logger.error("Failed to update charge with event data.")


async def find_next_unlinked_event():
    my_logger.debug("Finding next unlinked event...")
    try:
        cur.execute(
            "select * from skoda.charge_events where charge_id is NULL order by event_timestamp ASC limit 1"
        )
        row = cur.fetchone()
        if row:
            my_logger.debug(f"Found unlinked charge: {row}")
            return row
        else:
            my_logger.debug("No unlinked charges found. ")
    except mariadb.Error as e:
        my_logger.error(f"Error fetching from database: {e}")
        conn.rollback()


async def start_charge_hour(hour, timestamp):
    my_logger.debug(f"Starting charge hour for {hour} at {timestamp}...")
    try:
        cur.execute(
            "UPDATE skoda.charge_hours set start_at=? where log_timestamp=?",
            (timestamp, f"{hour}:00:00"),
        )
        conn.commit()
        my_logger.debug("Charge hour started successfully.")
    except mariadb.Error as e:
        my_logger.error(f"Error starting charge hour: {e}")
        conn.rollback()


async def is_charge_hour_started(hour):
    my_logger.debug(f"Checking if charge hour {hour} has started...")
    try:
        cur.execute(
            "SELECT * FROM skoda.charge_hours WHERE log_timestamp = ? and start_at IS NOT NULL",
            (f"{hour}:00:00",),
        )
        row = cur.fetchone()
        if row:
            my_logger.debug(f"Charge hour {hour} is already started.")
            return True
        else:
            my_logger.debug(f"Charge hour {hour} is not started, will update.")
            return False
    except mariadb.Error as e:
        my_logger.error(f"Error checking charge hour: {e}")
        conn.rollback()
        return False


async def locate_charge_hour(hour):
    my_logger.debug(f"Locating charge for hour: {hour}")
    try:
        cur.execute(
            "SELECT * FROM skoda.charge_hours where log_timestamp = ?",
            (f"{hour}:00:00",),
        )
        row = cur.fetchone()
        if row:
            my_logger.debug(f"Found charge hour: {row[0]}")
            return row[0]
        else:
            my_logger.debug("No charge hour found for this hour. Creating a new one.")
            await create_charge_event(hour)
            my_logger.debug("New charge hour created.")
            my_logger.debug(f"Looping to locate charge event for hour: {hour}")
            return await locate_charge_hour(hour)
    except mariadb.Error as e:
        my_logger.error(f"Error fetching charge event: {e}")
        conn.rollback()
        return None


async def create_charge_event(hour):
    try:
        my_logger.debug(f"Creating charge event for hour: {hour}")
        cur.execute(
            "INSERT INTO skoda.charge_hours (log_timestamp) VALUES (?)",
            (f"{hour}:00:00",),
        )
        conn.commit()
        my_logger.debug("Charge event created successfully.")
    except mariadb.Error as e:
        my_logger.error(f"Error creating charge event: {e}")
        conn.rollback()


async def link_charge_to_event(charge, event_id):
    try:
        my_logger.debug(f"Linking charge {charge} to event {event_id}")
        cur.execute(
            "UPDATE skoda.charge_events SET charge_id = ? WHERE id = ?",
            (event_id, charge[0]),
        )
        conn.commit()
        my_logger.debug("Charge linked to event successfully.")
        return True
    except mariadb.Error as e:
        my_logger.error(f"Error linking charge to event: {e}")
        conn.rollback()
        return False


async def keep_going_across_hours(lasthour, hour):
    my_logger.debug("Keeping charge hour across hours from %s to %s", lasthour, hour)
    try:
        my_logger.debug(
            "Updating charge hour %s to stop at %s:59:59", lasthour, lasthour
        )
        cur.execute(
            "UPDATE skoda.charge_hours set stop_at = ? where log_timestamp = ?",
            (lasthour + ":59:59", lasthour + ":00:00"),
        )
        conn.commit()
        my_logger.debug("Charge hour updated successfully.")
    except mariadb.Error as e:
        my_logger.error(f"Error updating charge hour: {e}")
        conn.rollback()
    my_logger.debug("starting the next hour at 00:00")
    await start_charge_hour(hour, f"{hour}:00:00")


async def update_charge_with_event_data(charge_id, charge):
    my_logger.debug("Updating event %s with charge data: %s", charge_id, charge)
    global lasthour
    global stillgoing
    try:
        my_logger.debug(
            "(in try except) Updating event %s with charge data: %s", charge_id, charge
        )
        hour = charge[1].strftime("%Y-%m-%d %H")
        if HOME_LATITUDE in str(charge[5]) and HOME_LONGITUDE in str(charge[6]):
            my_logger.debug("Charge is at home location")
            position = "home"
        else:
            my_logger.debug("Charge is not at home location")
            position = "away"
        if stillgoing and lasthour != hour:
            my_logger.debug(
                "Still going across hours, updating last hour %s to %s", lasthour, hour
            )
            await keep_going_across_hours(lasthour, hour)
            check_if_charge_hour_started = await is_charge_hour_started(hour)
            if not check_if_charge_hour_started:
                await start_charge_hour(hour, hour + ":00:00")
        if charge[2] == "start":
            check_if_charge_hour_started = await is_charge_hour_started(hour)
            if not check_if_charge_hour_started:
                await start_charge_hour(hour, charge[1])
            stillgoing = True
            lasthour = hour
        if charge[2] == "stop":
            my_logger.debug("Charge event is a stop event")
            check_if_charge_hour_started = await is_charge_hour_started(hour)
            if not check_if_charge_hour_started:
                await start_charge_hour(hour, charge[1])

            stillgoing = False
            stop_at = charge[1]

            cur.execute(
                "UPDATE skoda.charge_hours SET position = ?, charged_range = ?, mileage = ?, soc = ?, stop_at = ? WHERE id = ? and stop_at is NULL",
                (
                    position,
                    charge[3],
                    charge[4],
                    charge[7],
                    stop_at,
                    charge_id,
                ),
            )
        else:
            cur.execute(
                "UPDATE skoda.charge_hours SET position = ?, charged_range = ?, mileage = ?, soc = ? WHERE id = ?",
                (
                    position,
                    charge[3],
                    charge[4],
                    charge[7],
                    charge_id,
                ),
            )
        conn.commit()
        my_logger.debug("Event updated with charge data successfully.")
        return True
    except mariadb.Error as e:
        my_logger.error(f"Error updating event with charge data: {e}")
        conn.rollback()
        return False


async def chargerunner():
    my_logger.debug("Starting main function...")

    while True:
        charge = None
        my_logger.debug("Running chargecollector...")
        charge = await find_next_unlinked_event()
        if charge:
            my_logger.debug("Found unlinked charge event, processing... ")
            my_logger.debug("Processing charge: %s", charge)
            hour = charge[1].strftime("%Y-%m-%d %H")
            charge_id = await locate_charge_hour(hour)
            my_logger.debug("Charge ID located: %s", charge_id)
            await update_charges_with_event(charge)
            my_logger.debug("Charge processed successfully.")
            sleeptime = 0.001
        else:
            my_logger.debug("No charge found to process.")
            sleeptime = 60

            # sleeptime = await fetch_and_store_charge()
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
    rows = cur.fetchall()
    last_25_lines_joined += "\n".join([str(row) for row in rows])

    return PlainTextResponse(last_25_lines_joined.encode("utf-8"))


background = asyncio.create_task(chargerunner())
