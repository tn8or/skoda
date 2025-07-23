import asyncio
import datetime
import json
import logging
import os
import time

import mariadb
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import PlainTextResponse

from commons import SLEEPTIME, db_connect, get_logger

HOME_LATITUDE = "55.547"
HOME_LONGITUDE = "11.222"
lasthour = ""
stillgoing = False

my_logger = get_logger("skodachargecollector")
my_logger.warning("Starting the application...")


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
    conn, cur = await db_connect(my_logger)
    my_logger.debug("Finding next unlinked event...")
    try:
        cur.execute(
            "select * from skoda.charge_events where charge_id is NULL order by event_timestamp ASC limit 1"
        )
        row = cur.fetchone()
        if row:
            my_logger.debug("Found unlinked charge: %s", row)
            return row
        else:
            my_logger.debug("No unlinked charges found.")
    except mariadb.Error as e:
        my_logger.error("Error fetching from database: %s", e)
        conn.rollback()


async def start_charge_hour(hour, timestamp):
    conn, cur = await db_connect(my_logger)
    my_logger.debug("Starting charge hour for %s at %s...", hour, timestamp)
    try:
        cur.execute(
            "UPDATE skoda.charge_hours set start_at=? where log_timestamp=?",
            (timestamp, f"{hour}:00:00"),
        )
        conn.commit()
        my_logger.debug("Charge hour started successfully.")
    except mariadb.Error as e:
        my_logger.error("Error starting charge hour: %s", e)
        conn.rollback()


async def is_charge_hour_started(hour):
    conn, cur = await db_connect(my_logger)
    my_logger.debug("Checking if charge hour %s has started...", hour)
    try:
        cur.execute(
            "SELECT * FROM skoda.charge_hours WHERE log_timestamp = ? and start_at IS NOT NULL",
            (f"{hour}:00:00",),
        )
        row = cur.fetchone()
        if row:
            my_logger.debug("Charge hour %s is already started.", hour)
            return True
        else:
            my_logger.debug("Charge hour %s is not started, will update.", hour)
            return False
    except mariadb.Error as e:
        my_logger.error("Error checking charge hour: %s", e)
        conn.rollback()
        return False


async def locate_charge_hour(hour):
    conn, cur = await db_connect(my_logger)
    my_logger.debug("Locating charge for hour: %s", hour)
    try:
        cur.execute(
            "SELECT * FROM skoda.charge_hours where log_timestamp = ?",
            (f"{hour}:00:00",),
        )
        row = cur.fetchone()
        if row:
            my_logger.debug("Found charge hour: %s", row[0])
            return row[0]
        else:
            my_logger.debug("No charge hour found for this hour. Creating a new one.")
            await create_charge_event(hour)
            my_logger.debug("New charge hour created.")
            my_logger.debug("Looping to locate charge event for hour: %s", hour)
            return await locate_charge_hour(hour)
    except mariadb.Error as e:
        my_logger.error("Error fetching charge event: %s", e)
        conn.rollback()
        return None


async def create_charge_event(hour):
    conn, cur = await db_connect(my_logger)
    try:
        my_logger.debug("Creating charge event for hour: %s", hour)
        cur.execute(
            "INSERT INTO skoda.charge_hours (log_timestamp) VALUES (?)",
            (f"{hour}:00:00",),
        )
        conn.commit()
        my_logger.debug("Charge event created successfully.")
    except mariadb.Error as e:
        my_logger.error("Error creating charge event: %s", e)
        conn.rollback()


async def link_charge_to_event(charge, event_id):
    conn, cur = await db_connect(my_logger)
    try:
        my_logger.debug("Linking charge %s to event %s", charge, event_id)
        cur.execute(
            "UPDATE skoda.charge_events SET charge_id = ? WHERE id = ?",
            (event_id, charge[0]),
        )
        conn.commit()
        my_logger.debug("Charge linked to event successfully.")
        return True
    except mariadb.Error as e:
        my_logger.error("Error linking charge to event: %s", e)
        conn.rollback()
        return False


async def keep_going_across_hours(lasthour, hour):
    my_logger.debug("Keeping charge hour across hours from %s to %s", lasthour, hour)
    conn, cur = await db_connect(my_logger)
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
        my_logger.error("Error updating charge hour: %s", e)
        conn.rollback()
    my_logger.debug("starting the next hour at 00:00")
    await start_charge_hour(hour, f"{hour}:00:00")


async def find_records_with_no_start_range():
    my_logger.debug("Finding records with no value in start_range field")
    conn, cur = await db_connect(my_logger)
    try:
        cur.execute(
            "SELECT log_timestamp FROM skoda.charge_hours WHERE start_range IS NULL ORDER BY log_timestamp ASC LIMIT 1"
        )
        row = cur.fetchone()
        if row:
            my_logger.debug("Found record with no start_range: %s", row[0])
            return row[0]
        else:
            my_logger.debug("No records found with no start_range.")
    except mariadb.Error as e:
        my_logger.error("Error fetching records: %s", e)
        conn.rollback()


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
        my_logger.error("Error updating event with charge data: %s", e)
        conn.rollback()
        return False


async def find_range_from_start(hour):
    my_logger.debug("Finding range from charge initialization for hour %s", hour)
    conn, cur = await db_connect(my_logger)
    cur.execute(
        "select log_message,log_timestamp from skoda.rawlogs where log_timestamp <= ? and log_message like '%charged_range%' order by log_timestamp desc limit 1",
        (f"{hour}",),
    )
    row = cur.fetchone()
    if row:
        my_logger.debug("Found range from start: %s", row)
        range_value = int(row[0].split("charged_range=")[1].split(",")[0].strip())
        my_logger.debug("updating charge_hour %s with value: %s", hour, range_value)
        cur.execute(
            "UPDATE charge_hours SET start_range = ? WHERE log_timestamp = ?",
            (range_value, hour),
        )
        conn.commit()
        my_logger.debug("Charge hour updated with start range successfully.")


async def find_empty_amount():
    my_logger.debug("Finding charge hours with empty amounts")
    conn, cur = await db_connect(my_logger)
    try:
        cur.execute("SELECT id FROM skoda.charge_hours WHERE amount IS NULL")
        row = cur.fetchone()
        my_logger.debug("Found charge-hour with null amount: %s", row)
        row = row[0] if row else None
        my_logger.debug("Returning charge hour ID: %s", row)
        return row
    except mariadb.Error as e:
        my_logger.error("Error fetching unlinked charge events: %s", e)
        conn.rollback()
        return None


async def calculate_and_update_charge_amount(charge_id):
    conn, cur = await db_connect(my_logger)
    my_logger.debug("Calculating charge amount for charge hour %s", charge_id)
    try:
        cur.execute(
            "SELECT start_at, stop_at FROM skoda.charge_hours WHERE id = ?",
            (charge_id,),
        )
        row = cur.fetchone()
        my_logger.debug(
            "Fetched start and stop times for charge hour %s: %s", charge_id, row
        )
        if row and row[0] and row[1]:
            start_at = row[0]
            stop_at = row[1]
            my_logger.debug(
                "Raw start_at: %s, stop_at: %s for charge hour %s",
                start_at,
                stop_at,
                charge_id,
            )
            # Use as-is if already datetime, else parse
            if isinstance(start_at, datetime.datetime):
                start_time = start_at
            else:
                start_time = datetime.datetime.strptime(start_at, "%Y-%m-%d %H:%M:%S")
            if isinstance(stop_at, datetime.datetime):
                stop_time = stop_at
            else:
                stop_time = datetime.datetime.strptime(stop_at, "%Y-%m-%d %H:%M:%S")
            my_logger.debug(
                "Parsed start time: %s, stop time: %s for charge hour %s",
                start_time,
                stop_time,
                charge_id,
            )
            duration = (stop_time - start_time).total_seconds() / 3600  # in hours
            amount = duration * 10.5  # Assuming 10.5 kW charging rate
            my_logger.debug(
                "Calculated duration: %s hours, amount: %s for charge hour %s",
                duration,
                amount,
                charge_id,
            )
            # Update the charge hour with the calculated amount
            my_logger.debug("Updating charge hour with calculated amount")
            my_logger.debug(
                "Executing SQL update for charge hour %s with amount %s",
                charge_id,
                amount,
            )
            cur.execute(
                "UPDATE skoda.charge_hours SET amount = ? WHERE id = ?",
                (amount, charge_id),
            )
            conn.commit()
            my_logger.debug(
                "Charge amount updated to %s for charge hour %s",
                amount,
                charge_id,
            )
        else:
            my_logger.debug("No valid start or stop time found for charge hour.")
            return 30
    except mariadb.Error as e:
        my_logger.error("Error calculating charge amount: %s", e)
        conn.rollback()


async def chargerunner():
    my_logger.debug("Starting main function...")
    sleeptime = SLEEPTIME

    while True:
        charge = None
        my_logger.debug("Running chargecollector...")
        charge = await find_next_unlinked_event()
        if charge:
            my_logger.debug("Found unlinked charge event, processing...")
            my_logger.debug("Processing charge: %s", charge)
            hour = charge[1].strftime("%Y-%m-%d %H")
            charge_id = await locate_charge_hour(hour)
            my_logger.debug("Charge ID located: %s", charge_id)
            await update_charges_with_event(charge)
            my_logger.debug("Charge processed successfully.")
            sleeptime = 0.001
        else:
            my_logger.debug("No charge found to process.")

        # Check if there are any charge hours with empty amounts
        empty_charge_id = await find_empty_amount()

        if empty_charge_id:
            my_logger.debug(
                "Found charge hour with empty amount, calculating amount..."
            )
            sleeptime = await calculate_and_update_charge_amount(empty_charge_id)
            my_logger.debug("Charge amount calculated and updated successfully.")
        else:
            my_logger.debug("No charge hours with empty amounts found.")

        # check if there are any charge hours with no start_range
        my_logger.debug("Checking for charge hours with no start_range...")
        no_start_range = await find_records_with_no_start_range()
        if no_start_range:
            my_logger.debug("Found charge hours with no start_range, updating...")
            await find_range_from_start(no_start_range)
            my_logger.debug("Charge hours updated with start_range successfully.")
            sleeptime = 0.001

        await asyncio.sleep(sleeptime if sleeptime else 10)


def read_last_n_lines(filename, n):
    with open(filename, "r") as file:
        lines = file.readlines()
        return lines[-n:]


app = FastAPI()


@app.get("/")
async def root():
    conn, cur = await db_connect(my_logger)
    last_25_lines = read_last_n_lines("app.log", 15)
    last_25_lines_joined = "".join(last_25_lines)
    try:
        cur.execute("SELECT COUNT(*) FROM skoda.charge_hours")
        count = cur.fetchone()[0]
        last_25_lines_joined += "\n\nTotal logs in database: %s\n" % count
        cur.execute(
            "SELECT * FROM skoda.charge_hours order by log_timestamp desc limit 10"
        )
    except mariadb.Error as e:
        my_logger.error("Error fetching from database: %s", e)
        conn.rollback()
        import os
        import signal

        os.kill(os.getpid(), signal.SIGINT)
    rows = cur.fetchall()
    last_25_lines_joined += "\n".join([str(row) for row in rows])

    return PlainTextResponse(last_25_lines_joined.encode("utf-8"))


background = asyncio.create_task(chargerunner())
