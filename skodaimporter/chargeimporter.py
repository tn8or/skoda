import asyncio
import importlib
import json
import logging
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

from commons import (CHARGEFINDER_URL, db_connect, get_logger, load_secret,
                     pull_api)

# Optional type-only imports to keep runtime import free when myskoda is missing
if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from myskoda import MySkoda as MySkodaType

    MySkodaEvent = Any
    MySkodaCharging = Any
    MySkodaHealth = Any
    MySkodaPositionType = Any
    MySkodaStatus = Any
VIN = ""
# Global myskoda client; initialized in skodarunner() when myskoda is available...
myskoda: Optional[Any] = None


def _mask_vin(vin: str) -> str:
    """Return a redacted VIN for safe logging."""
    if not vin:
        return "<empty>"
    if len(vin) <= 6:
        return "***"
    return f"{vin[:3]}***{vin[-3:]}"
my_logger = get_logger("skodaimporter")
my_logger.warning("Starting the application...")
last_event_timeout = 4 * 60 * 60
last_event_received = time.time()
# Health state tracking for background task and API errors.
_bg_task: Optional[asyncio.Task] = None
_last_bg_error_time: float = 0.0
_last_bg_error_msg: Optional[str] = None
_degraded_reason: Optional[str] = None
_startup_ready: bool = False
_startup_started_at: float = time.time()
_startup_status: str = "starting"
_last_mqtt_error_msg: Optional[str] = None
_last_mqtt_error_time: float = 0.0
_polling_fallback_active: bool = False

# Enhanced connection detection configuration
CONNECTION_STATUS_TIMEOUT = 15 * 60  # 15 minutes for connection status checks
API_HEALTH_CHECK_TIMEOUT = 30 * 60  # 30 minutes for API health checks
MQTT_STATE_CHECK_INTERVAL = 5 * 60  # 5 minutes for MQTT state verification
# Grace window to avoid container restarts on transient auth failures
AUTH_GRACE_SECONDS = 15 * 60
VIN_LOOKUP_TIMEOUT_SECONDS = 20
STARTUP_READY_GRACE_SECONDS = 90
MYSKODA_CONNECT_TIMEOUT_SECONDS = 60
POLLING_FALLBACK_INTERVAL_SECONDS = 5 * 60
MQTT_RECOVERY_ATTEMPT_INTERVAL_SECONDS = 10 * 60
MQTT_ERROR_STALE_SECONDS = 120


class _MyskodaMqttLogHandler(logging.Handler):
    """Capture MQTT client failures emitted by myskoda for health diagnostics."""

    def emit(self, record: logging.LogRecord) -> None:
        global _last_mqtt_error_msg, _last_mqtt_error_time
        msg = record.getMessage()
        lower = msg.lower()
        if "connection lost" in lower or "not authorized" in lower:
            _last_mqtt_error_msg = msg
            _last_mqtt_error_time = time.time()


def _configure_myskoda_mqtt_logging() -> None:
    logger = logging.getLogger("myskoda.mqtt")
    logger.setLevel(logging.INFO)
    if any(isinstance(h, _MyskodaMqttLogHandler) for h in logger.handlers):
        return
    logger.addHandler(_MyskodaMqttLogHandler())


def _is_transient_auth_error(msg: Optional[str]) -> bool:
    if not msg:
        return False
    return (
        "AuthorizationFailedError" in msg
        or "MarketingConsentError" in msg
        or "authorization failed" in msg.lower()
        or "myskoda connect timed out" in msg.lower()
    )


def _mark_unhealthy(msg: str, exc: Optional[BaseException] = None) -> None:
    """Mark service as unhealthy and log the reason including stack traces."""
    global _last_bg_error_time, _last_bg_error_msg
    detail = msg
    if exc is not None:
        detail = f"{msg} ({exc.__class__.__name__}: {exc})"
    _last_bg_error_time = time.time()
    _last_bg_error_msg = detail
    if exc is not None:
        my_logger.exception("Unhealthy: %s", detail)
    else:
        my_logger.error("Unhealthy: %s", detail)


async def check_vehicle_connection_status(vin: str) -> bool:
    """Check vehicle connection status using MySkoda API.

    Returns True if vehicle is connected, False otherwise.
    This provides more reliable connection info than just timing events.
    """
    try:
        if myskoda is None:
            my_logger.warning(
                "MySkoda client not available for connection status check"
            )
            return False

        connection_status = await myskoda.get_connection_status(vin)
        my_logger.debug("Vehicle connection status: %s", connection_status)
        await save_log_to_db(f"Vehicle connection status check: {connection_status}")

        # Check if vehicle is actually connected (implementation depends on connection_status structure)
        # For now, we'll consider any successful response as connected
        return True

    except Exception as e:
        my_logger.warning("Failed to check vehicle connection status: %s", e)
        await save_log_to_db(f"Vehicle connection status check failed: {e}")
        return False


async def check_api_health(vin: str) -> bool:
    """Perform API health check by testing actual response.

    Returns True if API is responsive, False otherwise.
    Tests with lightweight API calls to verify connectivity.
    """
    try:
        if myskoda is None:
            my_logger.warning("MySkoda client not available for API health check")
            return False

        # Try a lightweight API call to verify responsiveness
        health = await myskoda.get_health(vin)
        my_logger.debug("API health check successful: mileage=%s", health.mileage_in_km)
        await save_log_to_db(
            f"API health check successful: mileage={health.mileage_in_km}"
        )
        return True

    except Exception as e:
        my_logger.warning("API health check failed: %s", e)
        await save_log_to_db(f"API health check failed: {e}")
        return False


async def attempt_mqtt_reconnect(vin: str) -> bool:
    """Try to re-establish MQTT streaming without restarting the service."""
    if myskoda is None:
        my_logger.error("MQTT reconnect skipped: MySkoda client not available")
        return False

    try:
        my_logger.info("Attempting MQTT re-subscribe before reconnect")
        myskoda.subscribe_events(on_event)
    except Exception as e:  # noqa: BLE001
        my_logger.warning("MQTT re-subscribe attempt failed: %s", e)

    try:
        await myskoda.connect(load_secret("SKODA_USER"), load_secret("SKODA_PASS"))
        myskoda.subscribe_events(on_event)
        await asyncio.sleep(1)  # give the client a moment to settle
        ok = check_mqtt_connection_state()
        if ok:
            await save_log_to_db("MQTT reconnect succeeded")
        else:
            await save_log_to_db("MQTT reconnect attempted but still disconnected")
        return ok
    except Exception as e:  # noqa: BLE001
        my_logger.warning("MQTT reconnect failed: %s", e)
        await save_log_to_db(f"MQTT reconnect failed: {e}")
        return False


def check_mqtt_connection_state() -> bool:
    """Check MQTT client connection state.

    Returns True if MQTT client is connected, False otherwise.
    Provides immediate feedback on MQTT connection status.
    """
    try:
        if myskoda is None or myskoda.mqtt is None:
            my_logger.debug("MQTT client not available")
            return False

        # Check if MQTT client is running and connected
        mqtt_client = myskoda.mqtt
        is_connected = (
            mqtt_client._running
            and mqtt_client._listener_task is not None
            and not mqtt_client._listener_task.done()
        )

        # A running listener task can still be in an auth/reconnect loop.
        # Treat recent broker-level auth/disconnect errors as disconnected.
        if is_connected and _last_mqtt_error_msg:
            recent_err = time.time() - _last_mqtt_error_time
            low = _last_mqtt_error_msg.lower()
            if (
                recent_err < MQTT_ERROR_STALE_SECONDS
                and ("not authorized" in low or "connection lost" in low)
            ):
                my_logger.warning(
                    "MQTT listener is running but recent broker error indicates disconnected state: %s",
                    _last_mqtt_error_msg,
                )
                return False

        my_logger.debug(
            "MQTT connection state: running=%s, connected=%s",
            mqtt_client._running,
            is_connected,
        )
        return is_connected

    except Exception as e:
        my_logger.warning("Failed to check MQTT connection state: %s", e)
        return False


async def perform_enhanced_connection_check(vin: str) -> dict:
    """Perform comprehensive connection health check.

    Returns a dict with results from multiple connection detection methods.
    This provides a more complete picture of connection health than timing alone.
    """
    results = {
        "timestamp": time.time(),
        "event_timeout_check": False,
        "vehicle_connection_check": False,
        "api_health_check": False,
        "mqtt_connection_check": False,
        "overall_healthy": False,
    }

    # 1. Original event timeout check
    elapsed = time.time() - last_event_received
    results["event_timeout_check"] = elapsed <= last_event_timeout
    results["last_event_elapsed_seconds"] = elapsed

    # 2. Vehicle connection status check (if not too recent)
    if elapsed > CONNECTION_STATUS_TIMEOUT:
        results["vehicle_connection_check"] = await check_vehicle_connection_status(vin)
    else:
        results["vehicle_connection_check"] = True  # Skip if recent event activity

    # 3. API health check (if event timeout exceeded)
    if elapsed > API_HEALTH_CHECK_TIMEOUT:
        results["api_health_check"] = await check_api_health(vin)
    else:
        results["api_health_check"] = True  # Skip if not timeout yet

    # 4. MQTT connection state check
    results["mqtt_connection_check"] = check_mqtt_connection_state()

    # Overall health assessment
    results["overall_healthy"] = (
        results["event_timeout_check"]
        and results["vehicle_connection_check"]
        and results["api_health_check"]
        and results["mqtt_connection_check"]
    )

    my_logger.debug("Enhanced connection check results: %s", results)
    await save_log_to_db(f"Enhanced connection check: {results}")

    return results


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

        # Resolve event enums from various possible module paths, with graceful fallback
        def _resolve_event_enums():
            candidates = (
                "myskoda.event",
                "myskoda.events",
                "myskoda.models.event",
                "myskoda.models.events",
            )
            for mod in candidates:
                try:
                    m = importlib.import_module(mod)
                    my_logger.debug("Resolved myskoda event enums from %s", mod)
                    return getattr(m, "EventType"), getattr(m, "ServiceEventTopic")
                except Exception:  # noqa: BLE001
                    continue
            return None, None

        _EventType, _ServiceEventTopic = _resolve_event_enums()

        # Determine event kind even if enums can't be imported
        def _is_service_event(t: Any) -> bool:
            if _EventType is not None:
                try:
                    return t == _EventType.SERVICE_EVENT
                except Exception:  # noqa: BLE001
                    pass
            name = getattr(t, "name", None)
            if isinstance(name, str):
                return name == "SERVICE_EVENT"
            return str(t).endswith("SERVICE_EVENT")

        def _is_charging_topic(topic: Any) -> bool:
            if _ServiceEventTopic is not None:
                try:
                    return topic == _ServiceEventTopic.CHARGING
                except Exception:  # noqa: BLE001
                    pass
            name = getattr(topic, "name", None)
            if isinstance(name, str):
                return name == "CHARGING"
            return str(topic).endswith("CHARGING")

        if _EventType is None or _ServiceEventTopic is None:
            my_logger.warning(
                "myskoda event enums not found; using name-based detection"
            )

        # Extract event_type and topic/name robustly across myskoda versions
        event_type_val = getattr(event, "event_type", None)
        if event_type_val is None:
            event_type_val = getattr(event, "type", None)

        # Helper to fetch event data object (supports both `event.data` and `data`)
        data_obj = getattr(getattr(event, "event", None), "data", None)
        if data_obj is None:
            data_obj = getattr(event, "data", None)

        # Determine event name and classify charging
        name_val = getattr(event, "name", None)
        name_str = getattr(name_val, "name", None)
        # Primary: topic enum if available
        topic_val = getattr(event, "topic", None)
        is_charging = False
        if topic_val is not None and _is_charging_topic(topic_val):
            is_charging = True
        # Fallbacks: presence of SOC or common charging fields in data
        if not is_charging and hasattr(data_obj, "soc"):
            is_charging = True
        if not is_charging and any(
            hasattr(data_obj, attr)
            for attr in ("charging_status", "plug_status", "is_charging")
        ):
            is_charging = True
        # Name-based hints (work with enums or plain strings)
        name_hint = name_str if isinstance(name_str, str) else str(name_val)
        if not is_charging and isinstance(name_hint, str):
            U = name_hint.upper()
            if "CHARG" in U or U in {
                "CHARGING",
                "CHARGING_STATUS_CHANGED",
                "START_CHARGING",
                "STOP_CHARGING",
            }:
                is_charging = True

        # Additional triggers:
        # 1) CHANGE_ACCESS service event should trigger a charge status fetch
        trigger_change_access = False
        if (
            isinstance(name_hint, str)
            and name_hint.upper() == "CHANGE_ACCESS"
            and _is_service_event(event_type_val)
        ):
            trigger_change_access = True

        # 2) Operation events like STOP_CHARGING when COMPLETED_SUCCESS
        op_val = getattr(event, "operation", None)
        op_name_raw = getattr(op_val, "name", None)
        op_hint = (
            op_name_raw
            if isinstance(op_name_raw, str)
            else (str(op_val) if op_val is not None else "")
        )
        status_val = getattr(event, "status", None)
        status_name_raw = getattr(status_val, "name", None)
        status_hint = (
            status_name_raw
            if isinstance(status_name_raw, str)
            else (str(status_val) if status_val is not None else "")
        )
        trigger_operation_stop_completed = False
        if op_hint:
            OU = op_hint.upper()
            SU = (
                status_hint.upper()
                if isinstance(status_hint, str)
                else str(status_hint).upper()
            )
            if ("CHARG" in OU or OU in {"STOP_CHARGING", "START_CHARGING"}) and SU in {
                "COMPLETED_SUCCESS"
            }:
                trigger_operation_stop_completed = True

        # Combine triggers
        is_charging = (
            is_charging or trigger_change_access or trigger_operation_stop_completed
        )

        is_service = _is_service_event(event_type_val)
        # Emit a classification log
        my_logger.debug(
            "Event classified: service=%s topic=%s name=%s op=%s status=%s charging=%s",
            is_service,
            getattr(getattr(topic_val, "name", topic_val), "name", topic_val),
            name_hint,
            op_hint,
            status_hint,
            is_charging,
        )

        if is_charging:
            # Touch Chargefinder and fetch latest charging snapshot
            api_result = await pull_api(CHARGEFINDER_URL, my_logger)
            my_logger.debug("API result: %s", api_result)
            soc_val = getattr(data_obj, "soc", None)
            await save_log_to_db(f"Charging event detected. SOC={soc_val}")
            try:
                await get_skoda_update(VIN)
            finally:
                charging = await myskoda.get_charging(VIN)
                my_logger.debug("Charging data fetched: %s", charging)
                await save_log_to_db(f"Charging data fetched: {charging}")
        else:
            # Informational only
            if is_service:
                my_logger.info(
                    "Ignoring non-charging service event: name=%s",
                    name_hint,
                )
            else:
                my_logger.info(
                    "Ignoring non-service event: event_type=%s name=%s",
                    getattr(event_type_val, "name", event_type_val),
                    name_hint,
                )
    except Exception as e:  # noqa: BLE001
        _mark_unhealthy(f"event processing failed: {e}", e)


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
        try:
            positions_resp = await myskoda.get_positions(vin)
            positions = getattr(positions_resp, "positions", None) or []
            # Prefer VEHICLE position when enum is available; otherwise first position
            pos = None
            if positions:
                if _PositionType is not None:
                    for p in positions:
                        try:
                            if p.type == _PositionType.VEHICLE:
                                pos = p
                                break
                        except Exception:  # noqa: BLE001
                            continue
                # Fallback to first if none matched
                if pos is None:
                    pos = positions[0]
            if pos is None:
                my_logger.warning("No vehicle positions available")
                await save_log_to_db("No vehicle positions available")
            else:
                my_logger.debug(
                    "lat: %s, lng: %s",
                    pos.gps_coordinates.latitude,
                    pos.gps_coordinates.longitude,
                )
                my_logger.debug("Vehicle positions fetched.")
                await save_log_to_db(
                    f"Vehicle positions fetched: lat: {pos.gps_coordinates.latitude}, lng: {pos.gps_coordinates.longitude}"
                )
        except Exception as e:
            # Do not mark unhealthy for positions-only issues; continue gracefully
            my_logger.warning("Fetching vehicle positions failed: %s", e)
            await save_log_to_db(f"Fetching vehicle positions failed: {e}")
    except Exception as e:  # noqa: BLE001
        _mark_unhealthy(f"get_skoda_update failed: {e}", e)
        raise


async def _resolve_vins_for_subscriptions() -> list[str]:
    """Resolve VINs to use for MQTT subscriptions.

    Prefer VINs returned by the account garage. If that list is empty,
    optionally fall back to SKODA_VEHICLE and rebind MQTT subscriptions for it.
    """
    if myskoda is None:
        return []

    global _degraded_reason
    try:
        vins = await asyncio.wait_for(
            myskoda.list_vehicle_vins(), timeout=VIN_LOOKUP_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        msg = (
            "Timed out while listing VINs from MySkoda garage "
            f"after {VIN_LOOKUP_TIMEOUT_SECONDS}s"
        )
        my_logger.warning(msg)
        await save_log_to_db(msg)
        _degraded_reason = msg
        vins = []
    except Exception as e:  # noqa: BLE001
        msg = f"Failed to list VINs from MySkoda garage: {e}"
        my_logger.warning(msg)
        await save_log_to_db(msg)
        _degraded_reason = msg
        vins = []

    if vins:
        _degraded_reason = None
        my_logger.info("Garage VIN lookup succeeded with %s VIN(s)", len(vins))
        return vins

    fallback_vin = (load_secret("SKODA_VEHICLE") or "").strip()
    if not fallback_vin:
        my_logger.warning(
            "No vehicle VINs found in garage and SKODA_VEHICLE is not configured"
        )
        await save_log_to_db(
            "No vehicle VINs found in garage and SKODA_VEHICLE is not configured"
        )
        return []

    msg = "No vehicle VINs found in garage; falling back to configured SKODA_VEHICLE"
    my_logger.warning(msg)
    _degraded_reason = msg
    await save_log_to_db("No garage VINs found; falling back to configured SKODA_VEHICLE")

    # MySkoda.connect() subscribes MQTT topics based on garage VINs.
    # If that list is empty we must rebind MQTT with the fallback VIN.
    try:
        user = await myskoda.get_user()
        if myskoda.mqtt is not None:
            await myskoda.mqtt.disconnect()
            await myskoda.mqtt.connect(user.id, [fallback_vin])
            my_logger.info("MQTT reconnected with fallback VIN subscription")
            await save_log_to_db("MQTT reconnected with fallback VIN subscription")
    except Exception as e:  # noqa: BLE001
        my_logger.warning("Failed to rebind MQTT using fallback VIN: %s", e)
        await save_log_to_db(f"Failed to rebind MQTT using fallback VIN: {e}")

    return [fallback_vin]


async def skodarunner() -> None:
    my_logger.debug("Starting main function...")
    # Reconnect loop with backoff on failures
    backoff = 5
    while True:
        try:
            # Lazy import MySkoda at runtime
            try:
                from myskoda import MySkoda as _MySkoda
                from myskoda.auth.authorization import AuthorizationFailedError
            except Exception as e:
                _mark_unhealthy(f"myskoda import failed: {e}", e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)
                continue

            _configure_myskoda_mqtt_logging()

            # Marketing consent handling became stricter upstream; import if available
            try:
                from myskoda.auth.authorization import MarketingConsentError
            except Exception:  # noqa: BLE001
                MarketingConsentError = None  # type: ignore
            async with ClientSession() as session:
                my_logger.debug("Creating MySkoda instance...")
                global myskoda
                global _startup_ready, _startup_started_at, _startup_status
                global _polling_fallback_active, last_event_received
                global VIN, _degraded_reason
                _startup_ready = False
                _polling_fallback_active = False
                if _startup_status == "starting":
                    _startup_started_at = time.time()
                _startup_status = "connecting"
                myskoda = _MySkoda(session)
                try:
                    await asyncio.wait_for(
                        myskoda.authorization.authorize(
                            load_secret("SKODA_USER"), load_secret("SKODA_PASS")
                        ),
                        timeout=MYSKODA_CONNECT_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    msg = (
                        "MySkoda authorization timed out after "
                        f"{MYSKODA_CONNECT_TIMEOUT_SECONDS}s"
                    )
                    _mark_unhealthy(msg)
                    _startup_status = msg
                    await save_log_to_db(msg)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 300)
                    continue
                except AuthorizationFailedError as auth_err:
                    # Upstream auth sometimes returns responses without redirect Location header
                    detail = (
                        "MySkoda authorization failed (missing redirect/Location header). "
                        "Verify SKODA_USER/SKODA_PASS and retry"
                    )
                    _mark_unhealthy(detail, auth_err)
                    await save_log_to_db(detail)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 300)
                    continue
                except Exception as auth_err:
                    # Marketing consent failures surface as MarketingConsentError in newer myskoda
                    if MarketingConsentError is not None and isinstance(
                        auth_err, MarketingConsentError
                    ):
                        detail = (
                            "MySkoda login requires marketing consent approval. "
                            "Open the provided consent URL in a browser, approve, then retry. "
                            f"URL: {auth_err}"
                        )
                        _mark_unhealthy(detail, auth_err)
                        await save_log_to_db(detail)
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 300)
                        continue
                    raise

                my_logger.debug("REST authorization succeeded")
                _startup_status = "resolving_user"
                user = await myskoda.get_user()
                my_logger.debug("Resolved user id for MQTT: %s", user.id)
                _startup_status = "resolving_vins"
                vins = await _resolve_vins_for_subscriptions()
                if not vins:
                    msg = (
                        "No vehicle VIN available for subscriptions "
                        "(garage empty and no SKODA_VEHICLE fallback)"
                    )
                    _mark_unhealthy(msg)
                    await save_log_to_db(msg)
                    raise RuntimeError(msg)
                for vin in vins:
                    print(f"Vehicle VIN: {_mask_vin(vin)}")
                    VIN = vin
                    await get_skoda_update(VIN)
                last_event_received = time.time()
                if vins:
                    my_logger.debug("Vehicle VIN available for account (index 0 selected)")
                else:
                    my_logger.warning("No vehicle VINs found for account")

                mqtt_ready = False
                if myskoda.mqtt is not None:
                    try:
                        await asyncio.wait_for(
                            myskoda.mqtt.connect(user.id, vins),
                            timeout=MYSKODA_CONNECT_TIMEOUT_SECONDS,
                        )
                        mqtt_ready = True
                        my_logger.debug("Connected to MySkoda MQTT")
                    except Exception as mqtt_err:  # noqa: BLE001
                        my_logger.warning(
                            "MQTT connect failed, switching to polling fallback: %s",
                            mqtt_err,
                        )
                        await save_log_to_db(
                            f"MQTT connect failed; switching to polling fallback: {mqtt_err}"
                        )
                else:
                    my_logger.warning(
                        "MySkoda MQTT client not available; using polling fallback"
                    )

                my_logger.debug("Subscribing to events...")
                if mqtt_ready:
                    try:
                        myskoda.subscribe_events(on_event)
                        _startup_ready = True
                        _startup_status = "ready"
                        _polling_fallback_active = False
                        _degraded_reason = None
                        my_logger.debug("Subscribed to events")
                    except Exception as e:  # noqa: BLE001
                        _mark_unhealthy(f"subscribe_events failed: {e}", e)
                        _startup_status = f"subscribe_failed: {e}"
                        raise
                else:
                    mqtt_hint = ""
                    if _last_mqtt_error_msg:
                        mqtt_hint = f" Latest MQTT error: {_last_mqtt_error_msg}"
                    fallback_msg = (
                        "MQTT unavailable, enabling API polling fallback."
                        f" Poll interval={POLLING_FALLBACK_INTERVAL_SECONDS}s."
                        f"{mqtt_hint}"
                    )
                    my_logger.warning(fallback_msg)
                    await save_log_to_db(fallback_msg)
                    _degraded_reason = fallback_msg
                    _polling_fallback_active = True
                    _startup_ready = True
                    _startup_status = "polling_fallback"
                # Reset backoff after a successful setup
                backoff = 5
                # Keep task alive until cancelled
                last_poll_ts = 0.0
                last_mqtt_recovery_attempt_ts = 0.0
                try:
                    while True:
                        now_ts = time.time()
                        if _polling_fallback_active and VIN:
                            if (
                                now_ts - last_poll_ts
                                >= POLLING_FALLBACK_INTERVAL_SECONDS
                            ):
                                try:
                                    await get_skoda_update(VIN)
                                    last_event_received = now_ts
                                    last_poll_ts = now_ts
                                    my_logger.info(
                                        "Polling fallback update completed for configured vehicle"
                                    )
                                except Exception as poll_err:  # noqa: BLE001
                                    my_logger.warning(
                                        "Polling fallback update failed: %s", poll_err
                                    )

                            if (
                                myskoda.mqtt is not None
                                and now_ts - last_mqtt_recovery_attempt_ts
                                >= MQTT_RECOVERY_ATTEMPT_INTERVAL_SECONDS
                            ):
                                last_mqtt_recovery_attempt_ts = now_ts
                                try:
                                    await myskoda.mqtt.connect(user.id, vins)
                                    myskoda.subscribe_events(on_event)
                                    _polling_fallback_active = False
                                    _degraded_reason = None
                                    _startup_status = "ready"
                                    my_logger.info(
                                        "MQTT recovery succeeded, disabling polling fallback"
                                    )
                                    await save_log_to_db(
                                        "MQTT recovery succeeded, polling fallback disabled"
                                    )
                                except Exception as recover_err:  # noqa: BLE001
                                    my_logger.warning(
                                        "MQTT recovery attempt failed: %s", recover_err
                                    )

                        await asyncio.sleep(1)
                except asyncio.CancelledError:
                    my_logger.info("Background task cancelled, shutting down...")
                    # Propagate cancellation to outer loop
                    raise
                finally:
                    try:
                        await myskoda.disconnect()
                    except Exception:  # noqa: BLE001
                        pass
        except asyncio.CancelledError:
            my_logger.info("Background runner cancelled, exiting...")
            break
        except Exception as e:  # noqa: BLE001
            _startup_ready = False
            _startup_status = f"error: {e}"
            _mark_unhealthy(f"background runner error: {e}", e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)


def read_last_n_lines(filename: str, n: int):
    try:
        with open(filename, "r", encoding="utf-8") as file:
            lines = file.readlines()
            return lines[-n:]
    except (FileNotFoundError, OSError):
        # In containers we log to stdout; local file logs may not exist.
        return []


app = FastAPI()


def _build_health_response(conn, cur, connection_results):
    elapsed = connection_results["last_event_elapsed_seconds"]
    my_logger.info(
        "All connection checks passed. Last event received %s seconds ago.",
        int(elapsed),
    )

    last_lines_joined = (
        "Container logs are emitted to stdout. Use kubectl logs for recent entries.\n"
    )

    last_lines_joined += "\n\nConnection Health Check Results:\n"
    for check, result in connection_results.items():
        if check != "timestamp":
            last_lines_joined += f"  {check}: {result}\n"

    last_lines_joined += (
        f"\nStartup readiness: {_startup_ready}\n"
        f"Startup status: {_startup_status}\n"
    )

    if _degraded_reason:
        last_lines_joined += (
            "\nStatus: DEGRADED\n"
            f"Reason: {_degraded_reason}\n"
        )

    if _polling_fallback_active:
        last_lines_joined += (
            "Polling fallback: enabled\n"
            f"Polling interval seconds: {POLLING_FALLBACK_INTERVAL_SECONDS}\n"
        )

    try:
        cur.execute("SELECT COUNT(*) FROM skoda.rawlogs")
        count = cur.fetchone()[0]
        last_lines_joined += f"\nTotal logs in database: {count}\n"
        cur.execute(
            "SELECT log_timestamp, log_message FROM skoda.rawlogs order by log_timestamp desc limit 10"
        )
        rows = cur.fetchall()
    except Exception as e:  # noqa: BLE001
        my_logger.error("Error fetching from database: %s", e)
        conn.rollback()
        rows = []
    for log_timestamp, log_message in rows:
        last_lines_joined += f"{log_timestamp} - {log_message}\n"
    return PlainTextResponse(last_lines_joined.encode("utf-8"))


@app.get("/")
async def root():
    conn, cur = await db_connect(my_logger)

    # Perform enhanced connection health check
    connection_results = await perform_enhanced_connection_check(VIN)

    # Explicit startup readiness gate: avoid reporting healthy before subscriptions are ready.
    startup_elapsed = time.time() - _startup_started_at
    if not _startup_ready and startup_elapsed > STARTUP_READY_GRACE_SECONDS:
        detail = (
            "startup incomplete: event subscriptions not ready "
            f"(status={_startup_status}, elapsed={int(startup_elapsed)}s)"
        )
        my_logger.error("Healthcheck failing: %s", detail)
        raise HTTPException(status_code=503, detail=detail)

    # If background task failed recently or is not running, report unhealthy
    recent_error_age = time.time() - _last_bg_error_time
    if _bg_task is None or _bg_task.done():
        detail = _last_bg_error_msg or "background task not running"
        my_logger.error("Healthcheck failing: %s", detail)
        raise HTTPException(status_code=503, detail=detail)
    # Treat transient startup/auth failures as transient; avoid 503 to prevent autoheal restarts
    if recent_error_age < 300:
        if (
            _is_transient_auth_error(_last_bg_error_msg)
            and recent_error_age < AUTH_GRACE_SECONDS
        ):
            my_logger.warning(
                "Transient upstream error within %ss; allowing grace period without restart",
                int(AUTH_GRACE_SECONDS),
            )
            # Return healthy response but include degraded note
            # Append a note to the response body via helper
            resp = _build_health_response(conn, cur, connection_results)
            # Add a short degraded note
            try:
                body = resp.body.decode("utf-8")
            except Exception:
                body = ""
            body += (
                "\nStatus: TRANSIENT-DEGRADED. "
                "Importer will retry with backoff; container kept healthy to avoid restart.\n"
            )
            return PlainTextResponse(body.encode("utf-8"))
        else:
            detail = _last_bg_error_msg or "recent background error"
            my_logger.error("Healthcheck failing: %s", detail)
            raise HTTPException(status_code=503, detail=detail)

    # Use enhanced connection check results
    if not connection_results["overall_healthy"]:
        my_logger.error("Enhanced connection check failed: %s", connection_results)

        # Try graduated response based on what failed
        if not connection_results["event_timeout_check"]:
            # Original timeout logic - try to refresh charging
            my_logger.error(
                "Last event more than %s hours old, triggering charge update",
                last_event_timeout / 3600,
            )
            try:
                charge_result = await myskoda.refresh_charging(VIN)
                if not charge_result:
                    my_logger.error(
                        "Failed to refresh charging data. Triggering restart"
                    )
                    raise HTTPException(
                        status_code=503,
                        detail="Service temporarily unavailable - charging refresh failed",
                    )
                else:
                    my_logger.debug("Charging refreshed: %s", charge_result)
            except Exception as e:
                my_logger.error("Exception during charging refresh: %s", e)
                raise HTTPException(
                    status_code=503,
                    detail=f"Service temporarily unavailable - refresh error: {e}",
                )

        elif not connection_results["api_health_check"]:
            # API health failed
            my_logger.error("API health check failed, service may be unresponsive")
            raise HTTPException(
                status_code=503,
                detail="Service temporarily unavailable - API health check failed",
            )

        elif not connection_results["mqtt_connection_check"]:
            # MQTT connection failed
            my_logger.error(
                "MQTT connection check failed, real-time events may be unavailable"
            )
            if _polling_fallback_active:
                my_logger.warning(
                    "Polling fallback active; treating MQTT failure as degraded but healthy"
                )
                return _build_health_response(conn, cur, connection_results)
            # Attempt to reconnect before failing health
            if await attempt_mqtt_reconnect(VIN):
                my_logger.info("MQTT reconnect succeeded; rechecking health")
                connection_results = await perform_enhanced_connection_check(VIN)
                if connection_results["overall_healthy"]:
                    return _build_health_response(conn, cur, connection_results)
            raise HTTPException(
                status_code=503,
                detail="Service temporarily unavailable - MQTT connection failed",
            )

        elif not connection_results["vehicle_connection_check"]:
            # Vehicle connection failed
            my_logger.error(
                "Vehicle connection check failed, vehicle may be unreachable"
            )
            raise HTTPException(
                status_code=503,
                detail="Service temporarily unavailable - vehicle connection failed",
            )

        else:
            # General failure
            raise HTTPException(
                status_code=503,
                detail="Service temporarily unavailable - connection checks failed",
            )
    else:
        return _build_health_response(conn, cur, connection_results)


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
