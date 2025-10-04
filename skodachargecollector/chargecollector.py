"""
Skoda Charge Collector Service.

This module processes charge events and calculates charging amounts for
the Skoda vehicle monitoring system. It links charge events to charge hours
and calculates billing amounts based on charging duration.
"""

import asyncio
import datetime
import os
import re
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Optional, Tuple

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

import mariadb
from commons import (
    SLEEPTIME,
    UPDATEALLCHARGES_URL,
    UPDATECHARGES_URL,
    db_connect,
    get_logger,
    pull_api,
)


@dataclass
class ChargeCollectorState:
    """State management for the charge collector."""

    last_hour: str = ""
    still_going: bool = False
    data_processed: int = 0


@dataclass
class LocationConfig:
    """Configuration for location detection."""

    home_latitude: str = "55.547"
    home_longitude: str = "11.222"


# Global instances
_collector_state = ChargeCollectorState()
_location_config = LocationConfig()
my_logger = get_logger("skodachargecollector")
my_logger.warning("Starting the application...")


async def update_charges_with_event(charge):
    my_logger.debug("Updating charges with event data from row %s", charge)
    hour = charge[1].strftime("%Y-%m-%d %H")
    my_logger.debug("Locating charge hour for %s", hour)
    event_id = await locate_charge_hour(hour)
    my_logger.debug("Charge hour located: %s", event_id)

    if event_id is None:
        my_logger.error("Cannot update charges - charge hour ID is None")
        return False

    if await update_charge_with_event_data(event_id, charge):
        my_logger.debug("Charge updated with event data successfully.")
        await link_charge_to_event(charge, event_id)
        return True
    else:
        my_logger.error("Failed to update charge with event data.")
        return False


async def find_next_unlinked_event() -> Optional[Tuple]:
    """
    Find the next charge event that hasn't been linked to a charge hour.

    Returns:
        Optional[Tuple]: The unlinked charge event data, or None if no
                        unlinked events are found.

    Raises:
        mariadb.Error: If database operation fails.
    """
    conn, cur = await db_connect(my_logger)
    my_logger.debug("Finding next unlinked event...")
    try:
        cur.execute(
            "SELECT * FROM skoda.charge_events WHERE charge_id IS NULL "
            "ORDER BY event_timestamp ASC LIMIT 1"
        )
        row = cur.fetchone()
        if row:
            my_logger.debug("Found unlinked charge: %s", row)
            return row
        else:
            my_logger.debug("No unlinked charges found.")
            return None
    except mariadb.Error as e:
        my_logger.error("Error fetching from database: %s", e)
        conn.rollback()
        raise


async def start_charge_hour(hour: str, timestamp: str) -> bool:
    """
    Mark the beginning of a charge hour with a timestamp.

    Args:
        hour: The hour in format "YYYY-MM-DD HH"
        timestamp: The exact timestamp when charging started

    Returns:
        bool: True if successful, False otherwise

    Raises:
        mariadb.Error: If database operation fails.
    """
    conn, cur = await db_connect(my_logger)
    my_logger.debug("Starting charge hour for %s at %s...", hour, timestamp)
    try:
        cur.execute(
            "UPDATE skoda.charge_hours SET start_at=? WHERE log_timestamp=?",
            (timestamp, f"{hour}:00:00"),
        )
        conn.commit()
        my_logger.debug("Charge hour started successfully.")
        return True
    except mariadb.Error as e:
        my_logger.error("Error starting charge hour: %s", e)
        conn.rollback()
        raise


async def is_charge_hour_started(hour: str) -> bool:
    """
    Check if a charge hour has already been started.

    Args:
        hour: The hour in format "YYYY-MM-DD HH"

    Returns:
        bool: True if the charge hour is started, False otherwise

    Raises:
        mariadb.Error: If database operation fails.
    """
    conn, cur = await db_connect(my_logger)
    my_logger.debug("Checking if charge hour %s has started...", hour)
    try:
        cur.execute(
            "SELECT * FROM skoda.charge_hours WHERE log_timestamp = ? "
            "AND start_at IS NOT NULL",
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
        raise


async def locate_charge_hour(hour: str) -> Optional[int]:
    """
    Locate or create a charge hour record for the given hour.

    Args:
        hour: The hour in format "YYYY-MM-DD HH"

    Returns:
        Optional[int]: The charge hour ID if found/created, None otherwise

    Raises:
        mariadb.Error: If database operation fails.
    """
    conn, cur = await db_connect(my_logger)
    try:
        my_logger.debug("Locating charge hour for %s", hour)
        cur.execute(
            "SELECT * FROM skoda.charge_hours WHERE log_timestamp = ?",
            (f"{hour}:00:00",),
        )
        row = cur.fetchone()
        if row:
            my_logger.debug("Found existing charge hour: %s", row[0])
            return row[0]
        else:
            my_logger.debug("Creating new charge hour for %s", hour)
            cur.execute(
                "INSERT INTO skoda.charge_hours (log_timestamp) VALUES (?)",
                (f"{hour}:00:00",),
            )
            conn.commit()

            # If lastrowid is None (common with UUID PKs), query for the just-inserted record
            if cur.lastrowid is None:
                my_logger.debug(
                    "lastrowid is None, querying for newly created charge hour"
                )
                cur.execute(
                    "SELECT id FROM skoda.charge_hours WHERE log_timestamp = ?",
                    (f"{hour}:00:00",),
                )
                row = cur.fetchone()
                if row:
                    charge_hour_id = row[0]
                    my_logger.debug(
                        "New charge hour created with ID: %s", charge_hour_id
                    )
                    return charge_hour_id
                else:
                    my_logger.error("Failed to retrieve newly created charge hour ID")
                    return None
            else:
                my_logger.debug("New charge hour created with ID: %s", cur.lastrowid)
                return cur.lastrowid
    except mariadb.Error as e:
        my_logger.error("Error locating charge hour: %s", e)
        conn.rollback()
        raise


async def create_charge_event(hour: str) -> Optional[int]:
    """
    Create a new charge event for the given hour.

    Args:
        hour: The hour in format "YYYY-MM-DD HH"

    Returns:
        Optional[int]: The charge event ID if created successfully, None otherwise

    Raises:
        mariadb.Error: If database operation fails.
    """
    conn, cur = await db_connect(my_logger)
    try:
        my_logger.debug("Creating charge event for hour: %s", hour)
        cur.execute(
            "INSERT INTO skoda.charge_hours (log_timestamp) VALUES (?)",
            (f"{hour}:00:00",),
        )
        conn.commit()
        my_logger.debug("Charge event created successfully.")
        return cur.lastrowid
    except mariadb.Error as e:
        my_logger.error("Error creating charge event: %s", e)
        conn.rollback()
        raise


async def link_charge_to_event(charge: Tuple, event_id: int) -> bool:
    """
    Link a charge record to a charge event.

    Args:
        charge: The charge data tuple
        event_id: The ID of the charge event to link to

    Returns:
        bool: True if successful, False otherwise

    Raises:
        mariadb.Error: If database operation fails.
    """
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
        raise


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
    conn, cur = await db_connect(my_logger)
    try:
        my_logger.debug(
            "(in try except) Updating event %s with charge data: %s", charge_id, charge
        )
        hour = charge[1].strftime("%Y-%m-%d %H")
        if _location_config.home_latitude in str(
            charge[5]
        ) and _location_config.home_longitude in str(charge[6]):
            my_logger.debug("Charge is at home location")
            position = "home"
        else:
            my_logger.debug("Charge is not at home location")
            position = "away"
        if _collector_state.still_going and _collector_state.last_hour != hour:
            my_logger.debug(
                "Still going across hours, updating last hour %s to %s",
                _collector_state.last_hour,
                hour,
            )
            await keep_going_across_hours(_collector_state.last_hour, hour)
            check_if_charge_hour_started = await is_charge_hour_started(hour)
            if not check_if_charge_hour_started:
                await start_charge_hour(hour, hour + ":00:00")
        if charge[2] == "start":
            check_if_charge_hour_started = await is_charge_hour_started(hour)
            if not check_if_charge_hour_started:
                await start_charge_hour(hour, charge[1])
            _collector_state.still_going = True
            _collector_state.last_hour = hour
        if charge[2] == "stop":
            my_logger.debug("Charge event is a stop event")
            check_if_charge_hour_started = await is_charge_hour_started(hour)
            if not check_if_charge_hour_started:
                # If we get a stop event without a start, set start_at to beginning of hour
                my_logger.warning(
                    "Stop event found without corresponding start event for hour %s, setting start_at to beginning of hour",
                    hour,
                )
                await start_charge_hour(hour, f"{hour}:00:00")
            _collector_state.still_going = False
            stop_at = charge[1]
            cur.execute(
                "UPDATE skoda.charge_hours SET position = ?, charged_range = ?, mileage = ?, soc = ?, stop_at = ? WHERE id = ? and stop_at is NULL",
                (position, charge[3], charge[4], charge[7], stop_at, charge_id),
            )
        else:
            cur.execute(
                "UPDATE skoda.charge_hours SET position = ?, charged_range = ?, mileage = ?, soc = ? WHERE id = ?",
                (position, charge[3], charge[4], charge[7], charge_id),
            )
        conn.commit()
        my_logger.debug("Event updated with charge data successfully.")
        return True
    except mariadb.Error as e:
        my_logger.error("Error updating event with charge data: %s", e)
        conn.rollback()
        return False


async def find_range_from_start(hour: str) -> Optional[bool]:
    """
    Find and update the start range for a charge hour.

    This function looks for the most recent charged_range value before
    the given hour and updates the charge_hours table with this value.

    Args:
        hour: The hour timestamp in format "YYYY-MM-DD HH:MM:SS"

    Returns:
        Optional[bool]: True if range found and updated successfully,
                       False if no range data found, None on database error

    Raises:
        mariadb.Error: If database operation fails
    """
    my_logger.debug("Finding range from charge initialization for hour %s", hour)
    conn, cur = await db_connect(my_logger)
    try:
        cur.execute(
            "SELECT log_message, log_timestamp FROM skoda.rawlogs "
            "WHERE log_timestamp <= ? AND log_message LIKE '%charged_range%' "
            "ORDER BY log_timestamp DESC LIMIT 1",
            (f"{hour}",),
        )
        row = cur.fetchone()

        if row:
            my_logger.debug("Found range from start: %s", row)

            # Extract range value from log message
            range_value = int(row[0].split("charged_range=")[1].split(",")[0].strip())

            my_logger.debug("Updating charge_hour %s with value: %s", hour, range_value)

            cur.execute(
                "UPDATE charge_hours SET start_range = ? WHERE log_timestamp = ?",
                (range_value, hour),
            )
            conn.commit()

            my_logger.debug("Charge hour updated with start range successfully.")
            return True  # Return True on successful update

        else:
            my_logger.debug("No range data found for hour %s", hour)
            return False  # Return False when no data found

    except mariadb.Error as e:
        my_logger.error("Error updating start range: %s", e)
        conn.rollback()
        return None  # Return None on database error


async def find_empty_amount():
    """
    Find a charge hour with an empty amount and no price.

    Returns:
        str: The ID of a charge hour needing amount calculation, or None if none found.
    """
    my_logger.debug("Finding charge hours with empty amounts")
    conn, cur = await db_connect(my_logger)
    try:
        # Find records where amount is NULL only (excluding amount = 0 and amount = -1)
        cur.execute(
            """
            SELECT id FROM skoda.charge_hours
            WHERE amount IS NULL
            LIMIT 1
            """
        )
        row = cur.fetchone()
        my_logger.debug("Found charge-hour with null amount: %s", row)
        row = row[0] if row else None
        my_logger.debug("Returning charge hour ID: %s", row)
        return row
    except mariadb.Error as e:
        my_logger.error("Error fetching unlinked charge events: %s", e)
        conn.rollback()
        return None


async def calculate_and_update_charge_amount(charge_id: str) -> Optional[int]:
    """
    Calculate and update the charge amount for a given charge hour.

    This function calculates the charging amount based on the duration between
    start_at and stop_at timestamps, using a rate of 10.5 per hour.

    Args:
        charge_id: The unique identifier for the charge hour record

    Returns:
        Optional[int]: SLEEPTIME if calculation successful, 30 if no valid
                      times found, None if database error occurred

    Raises:
        mariadb.Error: If database operation fails
    """
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

            # Parse datetime objects or strings
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

            # Calculate duration in hours
            duration = (stop_time - start_time).total_seconds() / 3600

            # Attempt to compute energy based on power readings from raw logs
            amount = None
            if duration < 0:
                my_logger.warning(
                    "Negative duration detected for charge hour %s: start=%s, stop=%s, duration=%s hours. Setting amount to 0.",
                    charge_id,
                    start_time,
                    stop_time,
                    duration,
                )
                amount = 0.0
            else:
                try:
                    amount = _compute_amount_from_power_readings(
                        cur, start_time, stop_time
                    )
                except (mariadb.Error, ValueError, TypeError) as e:
                    my_logger.warning(
                        "Power-based calculation failed for %s: %s (falling back to 10.5kW heuristic)",
                        charge_id,
                        e,
                    )
                    amount = None

                # Fallback to heuristic if we couldn't compute from power logs
                if amount is None:
                    amount = duration * 10.5

            # Verify with SoC if battery capacity is provided
            try:
                _verify_energy_with_soc(cur, start_time, stop_time, amount)
            except mariadb.Error as e:
                my_logger.warning("SoC verification failed due to DB error: %s", e)

            my_logger.debug(
                "Calculated duration: %s hours, amount: %s for charge hour %s",
                duration,
                amount,
                charge_id,
            )

            # Update the database with calculated amount
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
                "Charge amount updated to %s for charge hour %s", amount, charge_id
            )
            return SLEEPTIME  # Return SLEEPTIME on successful calculation

        else:
            my_logger.debug("No valid start or stop time found for charge hour.")
            return 30  # Return 30 seconds when no valid times found

    except mariadb.Error as e:
        my_logger.error("Error calculating charge amount: %s", e)
        conn.rollback()
        return None  # Return None on database error


async def invoke_charge_collector():
    """
    Main charge collector logic that processes unlinked charge events.

    Returns:
        int: Sleep time in seconds before next iteration
    """
    charge = None
    sleeptime = SLEEPTIME
    processed_count = 0
    my_logger.debug("Running chargecollector...")

    # Process all unlinked events in a loop
    while True:
        charge = await find_next_unlinked_event()
        if not charge:
            my_logger.debug("No more unlinked charges found to process.")
            break

        my_logger.debug("Found unlinked charge event, processing...")
        my_logger.debug("Processing charge: %s", charge)
        hour = charge[1].strftime("%Y-%m-%d %H")
        charge_id = await locate_charge_hour(hour)
        my_logger.debug("Charge ID located: %s", charge_id)

        if charge_id is not None:
            success = await update_charges_with_event(charge)
            if success:
                my_logger.debug("Charge processed successfully.")
                processed_count += 1
                sleeptime = 0.001
            else:
                my_logger.error("Failed to process charge event, will retry")
                sleeptime = 1
                break  # Stop processing on failure to avoid infinite loop
        else:
            my_logger.error(
                "Failed to locate or create charge hour, skipping this charge event"
            )
            # Still set short sleep to retry quickly
            sleeptime = 1
            break  # Stop processing on failure to avoid infinite loop

    if processed_count > 0:
        my_logger.debug("Processed %d unlinked charge events.", processed_count)

    # Step 2: Calculate amounts - first try single record, then batch process if needed
    empty_charge_id = await find_empty_amount()
    if empty_charge_id:
        my_logger.debug("Found charge hour with empty amount, calculating amount...")
        sleeptime = await calculate_and_update_charge_amount(empty_charge_id)
        my_logger.debug("Charge amount calculated and updated successfully.")

        # Check if there are more empty amounts and batch process them
        remaining_empty = await find_empty_amount()
        if remaining_empty:
            my_logger.debug(
                "More empty amounts found, batch processing all remaining..."
            )
            result = await process_all_amounts()
            my_logger.debug("Batch processing result: %s", result)
            sleeptime = 0.001  # Quick cycle after batch processing
    else:
        my_logger.debug("No charge hours with empty amounts found.")

    # Step 3: Populate start_range - first try single record, then batch process if needed
    my_logger.debug("Checking for charge hours with no start_range...")
    no_start_range = await find_records_with_no_start_range()
    if no_start_range:
        my_logger.debug("Found charge hours with no start_range, updating...")
        result = await find_range_from_start(no_start_range)
        if result:
            my_logger.debug("Charge hours updated with start_range successfully.")
            sleeptime = 0.001

            # Check if there are more records needing start_range and batch process them
            remaining_no_start_range = await find_records_with_no_start_range()
            if remaining_no_start_range:
                my_logger.debug(
                    "More records need start_range, batch processing all remaining..."
                )
                result = await process_all_start_ranges()
                my_logger.debug("Start range batch processing result: %s", result)
                sleeptime = 0.001  # Quick cycle after batch processing

    # Check if we need to invoke the API call after processing data
    if sleeptime != SLEEPTIME:
        my_logger.debug(
            "SLEEPTIME was changed to %s, flipping data_processed to 1 to invoke API call",
            sleeptime,
        )
        _collector_state.data_processed = 1

    # Always check if we need to call the API, regardless of current processing
    if _collector_state.data_processed == 1:
        my_logger.debug(
            "data_processed is 1, invoke API call to chargeprices and reset the flag"
        )
        _collector_state.data_processed = 0
        # Fire API call as background task to avoid blocking the main workflow
        asyncio.create_task(call_update_charges_api())

    return sleeptime


async def call_update_charges_api():
    """
    Background task to repeatedly call the update charges API until all prices are updated.
    """
    try:
        my_logger.debug("Starting background API calls to update all charge prices")
        updates_made = 0
        max_updates = 200  # Safety limit to prevent infinite loops

        while updates_made < max_updates:
            # Check how many records need price updates before the API call
            records_needing_updates = await count_records_needing_price_updates()
            if records_needing_updates == 0:
                my_logger.debug("No more records need price updates")
                break

            my_logger.debug(
                "Making API call #%d to update charges (%d records remaining)",
                updates_made + 1,
                records_needing_updates,
            )

            # Try bulk endpoint first, then fall back to likely endpoints and accept any 2xx response
            try:
                import httpx

                async with httpx.AsyncClient() as client:
                    # Prefer the bulk updater if available
                    resp = await client.get(
                        "http://skodaupdatechargeprices:80/update-all-charges"
                    )
                    if resp.status_code // 100 != 2:
                        # Fallback to single-update endpoint
                        resp = await client.get(
                            "http://skodaupdatechargeprices:80/update-charges"
                        )
                    if resp.status_code // 100 != 2:
                        # Fallback to root
                        resp = await client.get("http://skodaupdatechargeprices:80/")
                    my_logger.debug("Update charges API status: %s", resp.status_code)
            except Exception as e:
                my_logger.warning("Update charges API call failed: %s", e)

            # Give the price service a brief moment to commit before recounting
            await asyncio.sleep(0.05)

            # Check if the number of records decreased
            records_after = await count_records_needing_price_updates()
            if records_after < records_needing_updates:
                updates_made += 1
                my_logger.debug(
                    "Successfully updated charge price #%d (%d records remaining)",
                    updates_made,
                    records_after,
                )
                # Small delay between API calls to avoid overwhelming the service
                await asyncio.sleep(0.1)
            else:
                # No records were updated, stop trying
                my_logger.warning(
                    "API call did not reduce number of records needing updates, stopping"
                )
                break

        my_logger.info(
            "Background API calls completed. Made %d price updates.", updates_made
        )
    except mariadb.Error as e:
        my_logger.error("Background API calls failed: %s", e)


async def count_records_needing_price_updates() -> int:
    """
    Count how many charge hour records have amounts but no prices.

    Returns:
        int: Number of records that need price updates
    """
    db_conn, cur = await db_connect(my_logger)
    try:
        cur.execute(
            "SELECT COUNT(*) FROM skoda.charge_hours WHERE price IS NULL AND amount IS NOT NULL"
        )
        count = cur.fetchone()[0]
        return count
    except mariadb.Error as e:
        my_logger.error("Error counting records needing price updates: %s", e)
        return 0


async def chargerunner():
    my_logger.debug("Starting main function...")
    sleeptime = SLEEPTIME
    while True:
        sleeptime = await invoke_charge_collector()
        my_logger.debug("Sleeping for %s seconds...", sleeptime)
        await asyncio.sleep(sleeptime)


def read_last_n_lines(filename: str, n: int) -> list:
    """Read the last n lines from a file."""
    with open(filename, "r", encoding="utf-8") as file:
        lines = file.readlines()
        return lines[-n:]


@asynccontextmanager
async def _lifespan(app: FastAPI):
    runner = asyncio.create_task(chargerunner())
    fixer = asyncio.create_task(_fix_negatives_on_startup())
    try:
        yield
    finally:
        for t in (runner, fixer):
            t.cancel()
            with suppress(asyncio.CancelledError):
                await t


app = FastAPI(lifespan=_lifespan)


async def _fix_negatives_on_startup() -> None:
    """Run the negative amounts/price fixer once at startup in the background."""
    try:
        msg = await fix_negative_amounts()
        my_logger.info("Startup negative amounts fix completed: %s", msg)
    except Exception as exc:  # Broad on purpose to avoid blocking startup
        my_logger.warning("Startup fix-negative-amounts failed: %s", exc)


@app.get("/collect-charges")
async def collect_charges():
    my_logger.debug("Received request to collect charges ")
    # Fire charge collection as background task to avoid blocking the response
    asyncio.create_task(invoke_charge_collector())
    return PlainTextResponse("Charge collection initiated.".encode("utf-8"))


@app.get("/process-all-amounts")
async def process_all_amounts():
    """Process all charge hours with empty amounts in batch."""
    my_logger.debug("Received request to process all empty amounts")

    processed_count = 0
    failed_count = 0
    max_failures = 10  # Prevent infinite loops on problematic records

    while True:
        empty_charge_id = await find_empty_amount()
        if not empty_charge_id:
            break

        my_logger.debug("Processing charge hour %s", empty_charge_id)
        result = await calculate_and_update_charge_amount(empty_charge_id)

        if result == SLEEPTIME:  # Successful calculation
            processed_count += 1
            failed_count = 0  # Reset failure counter on success
            my_logger.debug(
                "Successfully processed charge hour %s (%d total)",
                empty_charge_id,
                processed_count,
            )
        elif result == 30:  # No valid start/stop times
            failed_count += 1
            my_logger.warning(
                "Skipping charge hour %s - no valid start/stop times (%d consecutive failures)",
                empty_charge_id,
                failed_count,
            )
            # Skip this record by setting amount to -1 to mark it as processed but invalid
            db_conn, cur = await db_connect(my_logger)
            try:
                cur.execute(
                    "UPDATE skoda.charge_hours SET amount = -1 WHERE id = ?",
                    (empty_charge_id,),
                )
                db_conn.commit()
                my_logger.debug(
                    "Marked charge hour %s as invalid (amount = -1)", empty_charge_id
                )
            except mariadb.Error as e:
                my_logger.error("Failed to mark charge hour as invalid: %s", e)

            if failed_count >= max_failures:
                my_logger.error(
                    "Too many consecutive failures, stopping batch processing"
                )
                break
        else:  # Database error (None)
            my_logger.error("Database error processing charge hour %s", empty_charge_id)
            break

    # Trigger price updates automatically if we changed any amounts
    if processed_count > 0:
        _collector_state.data_processed = 1
        asyncio.create_task(call_update_charges_api())

    message = f"Batch processing completed. Processed {processed_count} charge hours, skipped {failed_count} invalid records."
    my_logger.info(message)
    return PlainTextResponse(message.encode("utf-8"))


@app.get("/process-all-start-ranges")
async def process_all_start_ranges_endpoint():
    """Process all charge hours with missing start_range values in batch."""
    result = await process_all_start_ranges()
    return PlainTextResponse(result.encode("utf-8"))


@app.get("/fix-negative-amounts")
async def fix_negative_amounts_endpoint():
    """Fix all charge hours with negative amounts by recalculating them."""
    result = await fix_negative_amounts()
    return PlainTextResponse(result.encode("utf-8"))


async def process_all_start_ranges():
    """Process all charge hours with missing start_range values in batch."""
    my_logger.debug("Received request to process all missing start_range values")

    processed_count = 0
    failed_count = 0
    max_failures = 10  # Prevent infinite loops on problematic records

    while True:
        missing_start_range = await find_records_with_no_start_range()
        if not missing_start_range:
            break

        my_logger.debug("Processing start_range for hour %s", missing_start_range)
        result = await find_range_from_start(missing_start_range)

        if result is True:  # Successful update
            processed_count += 1
            failed_count = 0  # Reset failure counter on success
            my_logger.debug(
                "Successfully processed start_range for %s (%d total)",
                missing_start_range,
                processed_count,
            )
        elif result is False:  # No range data found
            failed_count += 1
            my_logger.warning(
                "Skipping hour %s - no range data found (%d consecutive failures)",
                missing_start_range,
                failed_count,
            )
            # Skip this record by setting start_range to -1 to mark it as processed but invalid
            db_conn, cur = await db_connect(my_logger)
            try:
                cur.execute(
                    "UPDATE skoda.charge_hours SET start_range = -1 WHERE log_timestamp = ?",
                    (missing_start_range,),
                )
                db_conn.commit()
                my_logger.debug(
                    "Marked hour %s as invalid (start_range = -1)", missing_start_range
                )
            except mariadb.Error as e:
                my_logger.error("Failed to mark hour as invalid: %s", e)

            if failed_count >= max_failures:
                my_logger.error(
                    "Too many consecutive failures, stopping start_range batch processing"
                )
                break
        else:  # Database error (None)
            my_logger.error(
                "Database error processing start_range for hour %s", missing_start_range
            )
            break

    message = f"Start range batch processing completed. Processed {processed_count} charge hours, skipped {failed_count} invalid records."
    my_logger.info(message)
    return message


async def fix_negative_amounts():
    """
    Fix all charge hours with negative amounts and negative prices.
    Then call the update prices endpoint to recalculate prices.
    """
    my_logger.debug("Starting to fix negative amounts and prices")

    conn, cur = await db_connect(my_logger)
    amount_fixed_count = 0
    amount_failed_count = 0
    price_fixed_count = 0

    try:
        # First, fix negative amounts by recalculating them
        cur.execute(
            "SELECT id, start_at, stop_at, amount FROM skoda.charge_hours WHERE amount < 0"
        )
        negative_amount_records = cur.fetchall()

        my_logger.info(
            "Found %d records with negative amounts", len(negative_amount_records)
        )

        for record in negative_amount_records:
            charge_id, start_at, stop_at, current_amount = record
            my_logger.debug(
                "Fixing negative amount for charge hour %s: current_amount=%s, start_at=%s, stop_at=%s",
                charge_id,
                current_amount,
                start_at,
                stop_at,
            )

            if start_at and stop_at:
                # Parse datetime objects or strings
                if isinstance(start_at, datetime.datetime):
                    start_time = start_at
                else:
                    start_time = datetime.datetime.strptime(
                        start_at, "%Y-%m-%d %H:%M:%S"
                    )

                if isinstance(stop_at, datetime.datetime):
                    stop_time = stop_at
                else:
                    stop_time = datetime.datetime.strptime(stop_at, "%Y-%m-%d %H:%M:%S")

                # Calculate correct duration and amount using power readings when possible
                duration = (stop_time - start_time).total_seconds() / 3600

                if duration < 0:
                    # Still negative, set to 0
                    new_amount = 0.0
                    my_logger.warning(
                        "Duration still negative for charge hour %s, setting amount to 0",
                        charge_id,
                    )
                else:
                    try:
                        computed = _compute_amount_from_power_readings(
                            cur, start_time, stop_time
                        )
                    except (mariadb.Error, ValueError, TypeError) as e:
                        my_logger.warning(
                            "Power-based recalculation failed for %s: %s (falling back to 10.5kW heuristic)",
                            charge_id,
                            e,
                        )
                        computed = None

                    new_amount = computed if computed is not None else duration * 10.5

                # Verify with SoC if battery capacity is provided
                try:
                    _verify_energy_with_soc(cur, start_time, stop_time, new_amount)
                except mariadb.Error as e:
                    my_logger.warning("SoC verification failed due to DB error: %s", e)

                # Update the record
                cur.execute(
                    "UPDATE skoda.charge_hours SET amount = ? WHERE id = ?",
                    (new_amount, charge_id),
                )

                my_logger.debug(
                    "Fixed charge hour %s: old_amount=%s, new_amount=%s, duration=%s hours",
                    charge_id,
                    current_amount,
                    new_amount,
                    duration,
                )
                amount_fixed_count += 1
            else:
                my_logger.warning(
                    "Cannot fix charge hour %s - missing start_at or stop_at times",
                    charge_id,
                )
                amount_failed_count += 1

        # Second, fix negative prices by setting them to NULL
        cur.execute("SELECT id, price FROM skoda.charge_hours WHERE price < 0")
        negative_price_records = cur.fetchall()

        my_logger.info(
            "Found %d records with negative prices", len(negative_price_records)
        )

        for record in negative_price_records:
            charge_id, current_price = record
            my_logger.debug(
                "Fixing negative price for charge hour %s: current_price=%s",
                charge_id,
                current_price,
            )

            # Set price to NULL so the charge price update function can recalculate it
            cur.execute(
                "UPDATE skoda.charge_hours SET price = NULL WHERE id = ?", (charge_id,)
            )

            my_logger.debug(
                "Fixed charge hour %s: old_price=%s, new_price=NULL",
                charge_id,
                current_price,
            )
            price_fixed_count += 1

        conn.commit()

        # Now call the bulk update prices endpoint to recalculate all prices
        try:
            result = await pull_api(UPDATEALLCHARGES_URL, my_logger)
            if result is None:
                # Fallback to single-update endpoint
                await pull_api(UPDATECHARGES_URL, my_logger)
        except Exception:
            # Last resort fallback
            await pull_api(UPDATECHARGES_URL, my_logger)

    except mariadb.Error as e:
        my_logger.error("Error fixing negative amounts and prices: %s", e)
        conn.rollback()
        raise

    message = f"Fixed negative amounts: {amount_fixed_count} amounts fixed, {amount_failed_count} amounts failed; {price_fixed_count} prices fixed. Update prices endpoint called."
    my_logger.info(message)
    return message


def _parse_charge_power(log_message: str) -> Optional[float]:
    """
    Extract charge_power_in_kw from a raw log message.

    The expected format contains a segment like 'charge_power_in_kw=90.0'.
    Returns a float power in kW if found, otherwise None.
    """
    try:
        match = re.search(r"charge_power_in_kw=([0-9]+(?:\.[0-9]+)?)", log_message)
        if match:
            return float(match.group(1))
        return None
    except (ValueError, TypeError):
        return None


def _compute_amount_from_power_readings(
    cur, start_time: datetime.datetime, stop_time: datetime.datetime
) -> Optional[float]:
    """
    Compute the energy (kWh) between start and stop by integrating power readings
    from skoda.rawlogs that contain 'Charging data fetched' with a charge_power_in_kw value.

    - Queries the last reading at or before start_time to seed the initial power.
    - Queries all readings between start_time and stop_time.
    - Approximates energy using piecewise-constant power between readings.

    Returns None when there are no usable readings so callers can fall back.
    """
    # Fetch the last reading before or at start_time
    cur.execute(
        """
        SELECT log_timestamp, log_message
        FROM skoda.rawlogs
                WHERE log_timestamp <= ?
                    AND log_message LIKE 'Charging data fetched:%'
                    AND log_message LIKE '%charge_power_in_kw=%'
        ORDER BY log_timestamp DESC
        LIMIT 1
        """,
        (start_time,),
    )
    before_row = cur.fetchone()

    # Fetch all readings within the interval
    cur.execute(
        """
        SELECT log_timestamp, log_message
        FROM skoda.rawlogs
                WHERE log_timestamp > ? AND log_timestamp <= ?
                    AND log_message LIKE 'Charging data fetched:%'
                    AND log_message LIKE '%charge_power_in_kw=%'
        ORDER BY log_timestamp ASC
        """,
        (start_time, stop_time),
    )
    within_rows = cur.fetchall() or []

    points: list[tuple[datetime.datetime, float]] = []

    # Seed with before reading if available
    if before_row is not None:
        ts, msg = before_row
        power = _parse_charge_power(str(msg))
        if power is not None:
            points.append((ts if isinstance(ts, datetime.datetime) else ts, power))

    # Add within readings
    for ts, msg in within_rows:
        power = _parse_charge_power(str(msg))
        if power is not None:
            points.append((ts if isinstance(ts, datetime.datetime) else ts, power))

    # No usable readings
    if not points:
        return None

    # Sort by timestamp to be safe
    points.sort(key=lambda x: x[0])

    # Integrate power over time within [start_time, stop_time]
    energy_kwh = 0.0
    for idx, (ts, power) in enumerate(points):
        # Current segment start is max(ts, start_time)
        seg_start = ts if ts > start_time else start_time

        # Determine segment end
        if idx + 1 < len(points):
            next_ts = points[idx + 1][0]
            seg_end = next_ts if next_ts < stop_time else stop_time
        else:
            seg_end = stop_time

        if seg_end <= seg_start:
            continue

        hours = (seg_end - seg_start).total_seconds() / 3600
        # Ensure non-negative power
        p = max(0.0, float(power))
        energy_kwh += p * hours

        # Early exit if we've reached the boundary
        if seg_end >= stop_time:
            break

    # Guard against tiny negatives due to floating errors
    if energy_kwh < 0:
        energy_kwh = 0.0

    return energy_kwh


def _parse_soc_percent(log_message: str) -> Optional[float]:
    """
    Extract state_of_charge_in_percent from a raw log message.

    Returns a float percentage [0..100] if found, else None.
    """
    try:
        match = re.search(
            r"state_of_charge_in_percent=([0-9]+(?:\.[0-9]+)?)", log_message
        )
        if match:
            return float(match.group(1))
        return None
    except (ValueError, TypeError):
        return None


def _compute_soc_based_energy(
    cur,
    start_time: datetime.datetime,
    stop_time: datetime.datetime,
    capacity_kwh: float,
) -> Optional[float]:
    """
    Estimate energy added (kWh) from SoC change using provided battery capacity.

    Approach:
    - Find the last SoC at or before start_time.
    - Find the last SoC at or before stop_time.
    - Energy ~= (soc_end - soc_start)/100 * capacity_kwh (min 0).
    Returns None if missing data.
    """
    # SoC at or before start
    cur.execute(
        """
        SELECT log_timestamp, log_message FROM skoda.rawlogs
        WHERE log_timestamp <= ?
          AND log_message LIKE 'Charging data fetched:%'
          AND log_message LIKE '%state_of_charge_in_percent=%'
        ORDER BY log_timestamp DESC
        LIMIT 1
        """,
        (start_time,),
    )
    row_start = cur.fetchone()

    # SoC at or before stop
    cur.execute(
        """
        SELECT log_timestamp, log_message FROM skoda.rawlogs
        WHERE log_timestamp <= ?
          AND log_message LIKE 'Charging data fetched:%'
          AND log_message LIKE '%state_of_charge_in_percent=%'
        ORDER BY log_timestamp DESC
        LIMIT 1
        """,
        (stop_time,),
    )
    row_end = cur.fetchone()

    if not row_start or not row_end:
        return None

    soc_start = _parse_soc_percent(str(row_start[1]))
    soc_end = _parse_soc_percent(str(row_end[1]))
    if soc_start is None or soc_end is None:
        return None

    delta_pct = max(0.0, soc_end - soc_start)
    return (delta_pct / 100.0) * float(capacity_kwh)


def _verify_energy_with_soc(
    cur, start_time: datetime.datetime, stop_time: datetime.datetime, energy_kwh: float
) -> None:
    """
    If SKODA_BATTERY_CAPACITY_KWH is set, compare power-based kWh vs SoC-based kWh.
    Logs an info line with both values and warns if discrepancy > 30%.
    """
    cap_env = os.environ.get("SKODA_BATTERY_CAPACITY_KWH")
    if not cap_env:
        return
    try:
        capacity_kwh = float(cap_env)
        if capacity_kwh <= 0:
            return
    except ValueError:
        return

    soc_kwh = _compute_soc_based_energy(cur, start_time, stop_time, capacity_kwh)
    if soc_kwh is None:
        return

    # Compute discrepancy percentage relative to power-based amount.
    baseline = max(energy_kwh, 0.0001)
    discrepancy = abs(energy_kwh - soc_kwh) / baseline * 100.0
    my_logger.info(
        "SoC verification: power_kwh=%.3f, soc_kwh=%.3f, discrepancy=%.1f%%",
        energy_kwh,
        soc_kwh,
        discrepancy,
    )
    if discrepancy > 30.0:
        my_logger.warning(
            "SoC vs power energy mismatch > 30%% (power=%.3f, soc=%.3f). Check readings or capacity.",
            energy_kwh,
            soc_kwh,
        )


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


if __name__ == "__main__":
    asyncio.run(chargerunner())
