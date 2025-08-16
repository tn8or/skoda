import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Optional

# Optional MariaDB import to allow module import without DB driver in CI
try:  # pragma: no cover - optional dependency handling
    import mariadb as _mariadb
except Exception:  # noqa: BLE001
    _mariadb = None  # type: ignore
from aiohttp import ClientSession
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from commons import CHARGEFINDER_URL, db_connect, get_logger, load_secret, pull_api

# Optional type-only imports to keep runtime import free when myskoda is missing
if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from myskoda import MySkoda as MySkodaType
    from myskoda.event import Event as MySkodaEvent
    from myskoda.models.charging import Charging as MySkodaCharging
    from myskoda.models.health import Health as MySkodaHealth
    from myskoda.models.position import PositionType as MySkodaPositionType
    from myskoda.models.status import Status as MySkodaStatus
VIN = ""
# Global myskoda client; initialized in skodarunner() when myskoda is available...
myskoda: Optional[Any] = None
my_logger = get_logger("skodaimporter")
my_logger.warning("Starting the application...")
last_event_timeout = 4 * 60 * 60
last_event_received = time.time()
# Health state tracking for background task and API errors.
_bg_task: Optional[asyncio.Task] = None
_last_bg_error_time: float = 0.0
_last_bg_error_msg: Optional[str] = None


def _mark_unhealthy(msg: str) -> None:
    """Mark service as unhealthy and log the reason."""
    global _last_bg_error_time, _last_bg_error_msg
    _last_bg_error_time = time.time()
    _last_bg_error_msg = msg
    my_logger.error("Unhealthy: %s", msg)


async def save_log_to_db(log_message: str) -> None:
    conn, cur = await db_connect(my_logger)
    try:
        cur.execute(
            "INSERT INTO rawlogs (log_message, log_timestamp) VALUES (?, NOW())",
            (log_message,),
        )
        conn.commit()
    except Exception as e:  # Fall back if MariaDB driver is not available
        my_logger.error("Error saving log to database: %s", e)
        conn.rollback()
    # Do not terminate the process on DB log failure; just rollback and continue


async def on_event(event: Any) -> None:
    global last_event_received
    try:
        event_json = json.dumps(event, default=str)
        my_logger.debug(event_json)
        await save_log_to_db(event_json)
        print(event)
        last_event_received = time.time()
        # Lazy import enums to avoid module import at import time
        try:
            from myskoda.event import EventType as _EventType
            from myskoda.event import ServiceEventTopic as _ServiceEventTopic
        except Exception:
            my_logger.error("myskoda not available; cannot process events")
            return
        if event.type == _EventType.SERVICE_EVENT:
            api_result = await pull_api(CHARGEFINDER_URL, my_logger)
            my_logger.debug("API result: %s", api_result)
            my_logger.debug("Received service event.")
            await save_log_to_db("Received service event.")
            if event.topic == _ServiceEventTopic.CHARGING:
                my_logger.debug("Battery is %s%% charged.", event.event.data.soc)
                await save_log_to_db(f"Battery is {event.event.data.soc}% charged.")
                await get_skoda_update(VIN)
                charging = await myskoda.get_charging(VIN)
                my_logger.debug("Charging data fetched.")
                await save_log_to_db(f"Charging data fetched: {charging}")
                my_logger.debug(charging)
    except Exception as e:  # noqa: BLE001
        _mark_unhealthy(f"event processing failed: {e}")


async def get_skoda_update(vin: str) -> None:
    try:
        my_logger.debug("Fetching vehicle health...")
        await save_log_to_db("Fetching vehicle health...")
        health = await myskoda.get_health(vin)
        my_logger.debug("Vehicle health fetched.")
        await save_log_to_db(f"Vehicle health fetched, mileage: {health.mileage_in_km}")
        my_logger.debug("Mileage: %s", health.mileage_in_km)
        info_data = await myskoda.get_info(vin)
        await save_log_to_db(f"Vehicle info fetched: {info_data}")
        my_logger.debug("Vehicle info fetched.")
        my_logger.debug(info_data)
        status = await myskoda.get_status(vin)
        my_logger.debug("Vehicle status fetched.")
        my_logger.debug(status)
        await save_log_to_db(f"Vehicle status fetched: {status}")
        my_logger.debug("Vehicle status fetched.")
        my_logger.debug("looking for positions...")
        # Lazy import for enum
        try:
            from myskoda.models.position import PositionType as _PositionType
        except Exception:
            _PositionType = None  # type: ignore
        pos = next(
            pos
            for pos in (await myskoda.get_positions(vin)).positions
            if (_PositionType is None) or (pos.type == _PositionType.VEHICLE)
        )
        my_logger.debug(
            "lat: %s, lng: %s",
            pos.gps_coordinates.latitude,
            pos.gps_coordinates.longitude,
        )
        my_logger.debug("Vehicle positions fetched.")
        await save_log_to_db(
            f"Vehicle positions fetched: lat: {pos.gps_coordinates.latitude}, lng: {pos.gps_coordinates.longitude}"
        )
    except Exception as e:  # noqa: BLE001
        _mark_unhealthy(f"get_skoda_update failed: {e}")
        raise


async def skodarunner() -> None:
    my_logger.debug("Starting main function...")
    # Reconnect loop with backoff on failures
    backoff = 5
    while True:
        try:
            # Lazy import MySkoda at runtime
            try:
                from myskoda import MySkoda as _MySkoda
            except Exception as e:
                _mark_unhealthy(f"myskoda import failed: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)
                continue
            async with ClientSession() as session:
                my_logger.debug("Creating MySkoda instance...")
                global myskoda
                myskoda = _MySkoda(session)
                await myskoda.connect(
                    load_secret("SKODA_USER"), load_secret("SKODA_PASS")
                )
                my_logger.debug("Connected to MySkoda")
                global VIN
                vins = await myskoda.list_vehicle_vins()
                for vin in vins:
                    print(f"Vehicle VIN: {vin}")
                    VIN = vin
                    await get_skoda_update(VIN)
                if vins:
                    my_logger.debug("Vehicle VIN: %s", vins[0])
                else:
                    my_logger.warning("No vehicle VINs found for account")
                my_logger.debug("Subscribing to events...")
                try:
                    myskoda.subscribe_events(on_event)
                    my_logger.debug("Subscribed to events")
                except Exception as e:  # noqa: BLE001
                    _mark_unhealthy(f"subscribe_events failed: {e}")
                    raise
                # Reset backoff after a successful setup
                backoff = 5
                # Keep task alive until cancelled
                try:
                    while True:
                        await asyncio.sleep(1)
                except asyncio.CancelledError:
                    my_logger.info("Background task cancelled, shutting down...")
                finally:
                    try:
                        await myskoda.disconnect()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as e:  # noqa: BLE001
            _mark_unhealthy(f"background runner error: {e}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)


def read_last_n_lines(filename: str, n: int):
    with open(filename, "r", encoding="utf-8") as file:
        lines = file.readlines()
        return lines[-n:]


app = FastAPI()


@app.get("/")
async def root():
    conn, cur = await db_connect(my_logger)
    # read-only access; no need for global declarations
    elapsed = time.time() - last_event_received
    # If background task failed recently or is not running, report unhealthy
    if _bg_task is None or _bg_task.done() or (time.time() - _last_bg_error_time) < 300:
        detail = _last_bg_error_msg or "background task not running"
        my_logger.error("Healthcheck failing: %s", detail)
        raise HTTPException(status_code=503, detail=detail)
    if elapsed > last_event_timeout:
        my_logger.error("Last event more than 1 hours old, triggering charge update")
        charge_result = await myskoda.refresh_charging(VIN)
        if not charge_result:
            my_logger.error("Failed to refresh charging data. Triggering restart")
            raise HTTPException(
                status_code=503, detail="Service temporarily unavailable"
            )
        else:
            my_logger.debug("Charging refreshed: %s", charge_result)
    else:
        my_logger.info(
            "Last event received %s seconds ago, within timeout.", int(elapsed)
        )
        last_25_lines = read_last_n_lines("app.log", 15)
        last_25_lines_joined = "".join(last_25_lines)
        try:
            cur.execute("SELECT COUNT(*) FROM skoda.rawlogs")
            count = cur.fetchone()[0]
            last_25_lines_joined += f"\n\nTotal logs in database: {count}\n"
            cur.execute(
                "SELECT log_timestamp, log_message FROM skoda.rawlogs order by log_timestamp desc limit 10"
            )
            rows = cur.fetchall()
        except Exception as e:
            my_logger.error("Error fetching from database: %s", e)
            conn.rollback()
            # Do not terminate the process on DB fetch failure; just rollback and return logs so far
            rows = []
        for log_timestamp, log_message in rows:
            last_25_lines_joined += f"{log_timestamp} - {log_message}\n"
        return PlainTextResponse(last_25_lines_joined.encode("utf-8"))


# Start/stop background runner with FastAPI lifespan
_bg_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _bg_task, myskoda
    # Startup: kick off background runner
    _bg_task = asyncio.create_task(skodarunner())
    try:
        yield
    finally:
        # Shutdown: cancel background task and disconnect client
        if _bg_task is not None:
            _bg_task.cancel()
            try:
                await _bg_task
            except asyncio.CancelledError:
                pass
        if myskoda is not None:
            try:
                await myskoda.disconnect()
            except Exception:  # noqa: BLE001
                pass


# Attach lifespan to the app
app.router.lifespan_context = lifespan
