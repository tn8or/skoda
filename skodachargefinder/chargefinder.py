import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import mariadb
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from commons import CHARGECOLLECTOR_URL, SLEEPTIME, db_connect, get_logger, pull_api


@dataclass
class ChargeEvent:
    """Represents a charging event for the vehicle."""

    timestamp: str
    pos_lat: Optional[float]
    pos_lon: Optional[float]
    charged_range: str
    mileage: Optional[str]
    event_type: str
    soc: str


class ChargeFinder:
    """Handles the logic for finding and processing vehicle charging events."""

    def __init__(self, logger: logging.Logger, db_connection: Tuple[Any, Any]):
        self.logger = logger
        self.conn, self.cur = db_connection
        self.last_soc = "0"
        self.last_range = "0"
        self.last_lat = "0"
        self.last_lon = "0"
        self.data_processed = False

    async def read_last_charge(self) -> Optional[Tuple]:
        """Fetch the most recent charge event from the database."""
        try:
            self.logger.debug("Fetching last charge from database...")
            self.cur.execute(
                "SELECT * FROM skoda.charge_events ORDER BY event_timestamp DESC LIMIT 1"
            )
            row = self.cur.fetchone()
            return row if row else None
        except mariadb.Error as e:
            self.logger.error("Error fetching last charge: %s", e)
            self.conn.rollback()
            return None

    async def find_vehicle_mileage(self, hour: str) -> Optional[str]:
        """Find vehicle mileage for a specific hour."""
        try:
            self.cur.execute(
                "SELECT log_message FROM skoda.rawlogs WHERE log_timestamp >= ? AND log_message LIKE '%mileage:%' ORDER BY log_timestamp ASC LIMIT 1",
                (f"{hour}:00:00",),
            )
            row = self.cur.fetchone()
            if row:
                mileage = row[0].split(":")[1].strip()
                self.logger.debug("Found mileage: %s", mileage)
                return mileage
            return None
        except mariadb.Error as e:
            self.logger.error("Error fetching vehicle mileage: %s", e)
            self.conn.rollback()
            return None

    def parse_charge_operation(self, log_message: str) -> str:
        """Parse the charging operation type from a log message."""
        if "READY_FOR_CHARGING" in log_message or "STOP_CHARGING" in log_message:
            return "stop"
        elif "CHARGING" in log_message:
            return "start"
        raise ValueError("No valid charge operation found in log message")

    def parse_charge_values(self, log_message: str) -> Tuple[str, str]:
        """Parse SOC and charged range values from a log message."""
        soc = self.last_soc
        charged_range = self.last_range

        if "soc=" in log_message:
            soc = log_message.split("soc=")[1].split(",")[0]
            self.last_soc = soc

        if "charged_range=" in log_message:
            charged_range = log_message.split("charged_range=")[1].split(",")[0]
            self.last_range = charged_range

        return soc, charged_range

    async def write_charge_to_db(self, charge: ChargeEvent) -> bool:
        """Write a charge event to the database."""
        try:
            self.cur.execute(
                """INSERT INTO skoda.charge_events
                   (event_timestamp, pos_lat, pos_lon, charged_range, mileage, event_type, soc)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    charge.timestamp,
                    charge.pos_lat,
                    charge.pos_lon,
                    charge.charged_range,
                    charge.mileage,
                    charge.event_type,
                    charge.soc,
                ),
            )
            self.conn.commit()
            return True
        except mariadb.Error as e:
            self.logger.error("Error writing charge to database: %s", e)
            self.conn.rollback()
            return False


lastsoc = 0
lastrange = 0
lastlat = 0
lastlon = 0
DATAPROCESSED = 0
my_logger = get_logger("skodachargefindlogger")
my_logger.warning("Starting the application...")


async def read_last_charge():
    conn, cur = await db_connect(my_logger)
    try:
        my_logger.debug("Fetching last charge from database...")
        cur.execute(
            "SELECT * FROM skoda.charge_events ORDER BY event_timestamp DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row:
            my_logger.debug("Last charge found: %s", row)
            return row
        else:
            my_logger.debug("No charges found in the database.")
            return None
    except mariadb.Error as e:
        my_logger.error("Error fetching last charge: %s", e)
        conn.rollback()
        return None


async def write_charge_to_db(charge):
    conn, cur = await db_connect(my_logger)
    try:
        my_logger.debug("Writing charge to database: %s", charge)
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
        my_logger.error("Error writing charge to database: %s", e)
        conn.rollback()


async def find_vehicle_mileage(hour):
    my_logger.debug("Finding vehicle mileage for %s:00", hour)
    conn, cur = await db_connect(my_logger)
    try:
        cur.execute(
            "SELECT log_message FROM skoda.rawlogs WHERE log_timestamp >= ? AND log_message LIKE '%mileage:%' ORDER BY log_timestamp ASC LIMIT 1",
            (f"{hour}:00:00",),
        )
        row = cur.fetchone()
        if row:
            my_logger.debug("Vehicle mileage found: %s", row)
            mileage = row[0].split(":")[1].strip()
            my_logger.debug("Returning mileage %s", mileage)
            return mileage
        else:
            my_logger.debug("No vehicle mileage found.")
            return None
    except mariadb.Error as e:
        my_logger.error("Error fetching vehicle mileage: %s", e)
        conn.rollback()
        return None


async def find_vehicle_position(hour):
    my_logger.debug("Finding vehicle position for %s:00", hour)
    conn, cur = await db_connect(my_logger)
    try:
        cur.execute(
            "SELECT log_message FROM skoda.rawlogs WHERE log_timestamp >= ? AND log_timestamp < ? AND log_message LIKE 'Vehicle positions%' ORDER BY log_timestamp DESC LIMIT 1",
            (f"{hour}:00:00", f"{hour}:59:59"),
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
            return position
        else:
            my_logger.debug("No vehicle position found.")
            return None
    except mariadb.Error as e:
        my_logger.error("Error fetching vehicle position: %s", e)
        conn.rollback()
        return None


async def fetch_and_store_charge() -> float:
    """
    Fetch and store new charging events from the raw logs.

    This function retrieves the latest charging events from the raw logs table,
    processes the data, and stores it in the charge_events table if the event
    is not from the current hour.

    Returns:
        float: Sleep time in seconds - 0.001 if data was processed,
               SLEEPTIME otherwise.
    """
    global lastsoc, lastrange, lastlat, lastlon

    my_logger.debug("Fetching and storing charge...")
    conn, cur = await db_connect(my_logger)

    last_stored_charge = await read_last_charge()
    if last_stored_charge:
        last_timestamp = last_stored_charge[1]
        my_logger.debug("Last stored charge timestamp: %s", last_timestamp)
    else:
        last_timestamp = 0

    query = (
        "SELECT * FROM skoda.rawlogs WHERE log_timestamp > ? "
        "and (log_message like '%ChargingState.CHARGING%' "
        "or log_message like '%ChargingState.READY_FOR_CHARGING%' "
        "or log_message like '%OperationName.STOP_CHARGING%') "
        "order by log_timestamp ASC LIMIT 1"
    )

    my_logger.debug(
        "Executing query: %s with last_timestamp: %s", query, last_timestamp
    )
    cur.execute(query, (last_timestamp,))
    new_charge_row = cur.fetchone()

    if not new_charge_row:
        my_logger.debug("No new charge found in rawlogs table.")
        return SLEEPTIME

    my_logger.debug("Charge row fetched: %s", new_charge_row)
    dt_str = new_charge_row[0].strftime("%Y-%m-%d %H:%M:%S")
    my_logger.debug("Charge timestamp: %s", dt_str)

    dt_str_split = dt_str.split(":")
    position = await find_vehicle_position(dt_str_split[0])
    my_logger.debug("Vehicle position found: %s", position)

    mileage = await find_vehicle_mileage(dt_str_split[0])
    my_logger.debug("Vehicle mileage found: %s", mileage)

    # Determine charge operation type
    operation = _parse_charge_operation(new_charge_row[1])
    my_logger.debug("Charge operation is '%s'", operation)

    # Parse SOC and charged range values
    soc, charged_range = _parse_charge_values(new_charge_row[1])

    # Handle position data
    if not position:
        my_logger.debug("No position found, using last known position.")
        position = [lastlat, lastlon]
    else:
        lastlat = position[0]
        lastlon = position[1]

    new_charge = {
        "timestamp": dt_str,
        "pos_lat": position[0] if position else None,
        "pos_lon": position[1] if position else None,
        "charged_range": charged_range,
        "mileage": mileage,
        "event_type": operation,
        "soc": soc,
    }

    my_logger.debug("New charge fetched: %s", new_charge)

    if last_timestamp == 0 or new_charge["timestamp"] != last_timestamp:
        my_logger.debug("New charge is newer than the last stored charge...")

        # Check if charge timestamp is not in the current hour
        current_hour = datetime.now().strftime("%Y-%m-%d %H")
        charge_hour = new_charge["timestamp"].split(":")[0]

        if charge_hour != current_hour:
            my_logger.debug(
                "Charge timestamp is not in the current hour, writing to DB"
            )
            await write_charge_to_db(new_charge)
            return 0.001
        else:
            my_logger.debug(
                "Charge timestamp is in the current hour, not writing to DB"
            )
            return SLEEPTIME
    else:
        my_logger.debug("No new charge to write, skipping...")
        return SLEEPTIME


def _parse_charge_operation(log_message: str) -> str:
    """
    Parse the charging operation type from a log message.

    Args:
        log_message (str): The log message containing charge operation data.

    Returns:
        str: 'stop' for charging stopped, 'start' for charging started.

    Raises:
        ValueError: If no valid charge operation is found in the message.
    """
    if "READY_FOR_CHARGING" in log_message or "STOP_CHARGING" in log_message:
        return "stop"
    elif "CHARGING" in log_message:
        return "start"
    else:
        raise ValueError(f"No charge operation found in: {log_message}")


def _parse_charge_values(log_message: str) -> Tuple[str, str]:
    """
    Parse SOC and charged range values from a log message.

    Args:
        log_message (str): The log message containing charge data.

    Returns:
        Tuple[str, str]: A tuple containing (soc, charged_range).
    """
    global lastsoc, lastrange

    if "soc=" in log_message:
        soc = log_message.split("soc=")[1].split(",")[0]
        lastsoc = soc
    else:
        soc = lastsoc

    if "charged_range=" in log_message:
        charged_range = log_message.split("charged_range=")[1].split(",")[0]
        lastrange = charged_range
    else:
        charged_range = lastrange

    return soc, charged_range


async def invoke_chargefinder() -> float:
    """
    Invoke the charge finder to process new charging events.

    This function fetches and stores new charging events, manages data processing
    flags, and triggers API calls to downstream services when needed.

    Returns:
        float: Sleep time in seconds before the next invocation.
    """
    my_logger.debug("Invoking chargefinder...")
    try:
        sleeptime = await fetch_and_store_charge()
        my_logger.debug("Result from fetch_and_store_charge: %s", sleeptime)

        if sleeptime != SLEEPTIME:
            my_logger.debug(
                "SLEEPTIME was changed to %s, flipping DATAPROCESSED to 1 to invoke API call",
                sleeptime,
            )
            global DATAPROCESSED
            DATAPROCESSED = 1
        else:
            my_logger.debug(
                "No data updated - check if we need to invoke API for further processing"
            )

            if DATAPROCESSED == 1:
                my_logger.debug(
                    "DATAPROCESSED is 1, invoke API call to chargecollector and reset the flag"
                )
                DATAPROCESSED = 0
                api_result = await pull_api(CHARGECOLLECTOR_URL, my_logger)
                my_logger.debug("API result: %s", api_result)

        return sleeptime
    except Exception as e:
        my_logger.error("Error in invoke_chargefinder: %s", e)
        return SLEEPTIME


async def chargerunner():
    my_logger.debug("Starting main function...")
    while True:
        my_logger.debug("Running chargerunner...")
        sleeptime = await invoke_chargefinder()
        await asyncio.sleep(sleeptime if sleeptime else SLEEPTIME)


def read_last_n_lines(filename, n):
    with open(filename, "r") as file:
        lines = file.readlines()
        return lines[-n:]


app = FastAPI()
my_logger.debug("FastAPI app initialized.")


@app.get("/find-charges")
async def find_charges():
    my_logger.debug("Received request to find charges... ")
    await invoke_chargefinder()
    return PlainTextResponse("Charge finder started.", status_code=200)


@app.get("/")
async def root():
    conn, cur = await db_connect(my_logger)
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
        my_logger.error("Error fetching from database: %s", e)
        conn.rollback()
        import os
        import signal

        os.kill(os.getpid(), signal.SIGINT)
    rows = cur.fetchall()
    last_25_lines_joined += "\n".join([str(row) for row in rows])
    return PlainTextResponse(last_25_lines_joined.encode("utf-8"))


background = asyncio.create_task(chargerunner())
