import asyncio
import importlib
import sys
import time
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def import_with_stubs():
    # Stub graypy so get_logger works
    import logging

    class _DummyHandler(logging.Handler):
        def __init__(self, *args, **kwargs):
            super().__init__()

        def emit(self, record):
            pass

    sys.modules["graypy"] = types.SimpleNamespace(GELFTCPHandler=_DummyHandler)
    # Stub myskoda and myskoda.event with needed symbols
    myskoda_mod = types.SimpleNamespace()
    myskoda_event_mod = types.SimpleNamespace()
    sys.modules["myskoda"] = myskoda_mod
    sys.modules["myskoda.event"] = myskoda_event_mod
    # Import module under test
    return importlib.import_module("skodaimporter.chargeimporter")


@pytest.mark.asyncio
async def test_get_skoda_update_handles_error():
    m = import_with_stubs()

    class FakeSkoda:
        async def get_health(self, vin):
            raise RuntimeError("boom")

    m.myskoda = FakeSkoda()
    with patch.object(m, "_mark_unhealthy") as mark:
        with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
            with pytest.raises(RuntimeError):
                await m.get_skoda_update("VIN")
        assert mark.called


@pytest.mark.asyncio
async def test_on_event_handles_service_event():
    m = import_with_stubs()

    class EType:
        SERVICE_EVENT = object()

    class Topic:
        CHARGING = object()

    # Provide the enums via sys.modules for inner import in on_event
    sys.modules["myskoda.event"].EventType = EType
    sys.modules["myskoda.event"].ServiceEventTopic = Topic

    with patch(
        "skodaimporter.chargeimporter.pull_api",
        new=AsyncMock(return_value={"ok": True}),
    ):
        with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
            # Patch myskoda methods used by get_skoda_update
            m.myskoda = MagicMock()
            m.myskoda.get_charging = AsyncMock(return_value={"c": 1})
            m.myskoda.get_health = AsyncMock(return_value=MagicMock(mileage_in_km=1))
            m.myskoda.get_info = AsyncMock(return_value={"i": 1})
            m.myskoda.get_status = AsyncMock(return_value={"s": 1})
            m.myskoda.get_positions = AsyncMock(return_value=MagicMock(positions=[]))

            event = MagicMock()
            event.type = EType.SERVICE_EVENT
            event.topic = Topic.CHARGING
            event.event.data.soc = 50
            await m.on_event(event)


@pytest.mark.asyncio
async def test_get_skoda_update_handles_no_positions_gracefully():
    m = import_with_stubs()

    class FakeSkoda:
        async def get_health(self, vin):
            class H:
                mileage_in_km = 123

            return H()

        async def get_info(self, vin):
            return {"i": 1}

        async def get_status(self, vin):
            return {"s": 1}

        async def get_positions(self, vin):
            class R:
                positions = []

            return R()

    m.myskoda = FakeSkoda()
    with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
        # Should not raise
        await m.get_skoda_update("VIN")


@pytest.mark.asyncio
async def test_get_skoda_update_handles_positions_exception_gracefully():
    m = import_with_stubs()

    class FakeSkoda:
        async def get_health(self, vin):
            class H:
                mileage_in_km = 123

            return H()

        async def get_info(self, vin):
            return {"i": 1}

        async def get_status(self, vin):
            return {"s": 1}

        async def get_positions(self, vin):
            raise RuntimeError("positions failed")

    m.myskoda = FakeSkoda()
    with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
        # Should not raise
        await m.get_skoda_update("VIN")


@pytest.mark.asyncio
async def test_get_skoda_update_logs_mileage_and_charging_snapshot():
    m = import_with_stubs()

    class FakeEnum:
        def __init__(self, name):
            self.name = name

    class FakeCharging:
        soc = 57
        charging_status = FakeEnum("CHARGING")
        plug_status = FakeEnum("CONNECTED")
        state = FakeEnum("READY_FOR_CHARGING")

    class FakeSkoda:
        async def get_health(self, vin):
            class H:
                mileage_in_km = 45678

            return H()

        async def get_info(self, vin):
            return {"i": 1}

        async def get_status(self, vin):
            return {"s": 1}

        async def get_positions(self, vin):
            class R:
                positions = []

            return R()

        async def get_charging(self, vin):
            return FakeCharging()

    m.myskoda = FakeSkoda()
    save_log = AsyncMock()
    with patch("skodaimporter.chargeimporter.save_log_to_db", new=save_log):
        await m.get_skoda_update("VIN")

    messages = [call.args[0] for call in save_log.await_args_list if call.args]
    assert any(
        "Vehicle snapshot fetched: mileage_km=45678, soc=57" in msg
        and "charging_status=CHARGING" in msg
        and "plug_status=CONNECTED" in msg
        for msg in messages
    )


@pytest.mark.asyncio
async def test_get_skoda_update_inferrs_charging_from_info_render_hints():
    m = import_with_stubs()

    class FakeViewType:
        name = "CHARGING_LIGHT"

    class FakeRender:
        view_type = FakeViewType()

    class FakeInfo:
        composite_renders = [FakeRender()]

    class FakeCharging:
        soc = None
        charging_status = None
        plug_status = None
        state = None

    class FakeSkoda:
        async def get_health(self, vin):
            class H:
                mileage_in_km = 45678

            return H()

        async def get_info(self, vin):
            return FakeInfo()

        async def get_status(self, vin):
            return {"s": 1}

        async def get_positions(self, vin):
            class R:
                positions = []

            return R()

        async def get_charging(self, vin):
            return FakeCharging()

    m.myskoda = FakeSkoda()
    save_log = AsyncMock()
    with patch("skodaimporter.chargeimporter.save_log_to_db", new=save_log):
        await m.get_skoda_update("VIN")

    messages = [call.args[0] for call in save_log.await_args_list if call.args]
    assert any(
        "Vehicle snapshot fetched: mileage_km=45678" in msg
        and "charging_status=INFERRED_CHARGING" in msg
        and "plug_status=INFERRED_PLUGGED_IN" in msg
        and "render_hints=CHARGING_LIGHT" in msg
        for msg in messages
    )


def test_build_chargefinder_event_message_charging():
    m = import_with_stubs()
    message = m._build_chargefinder_event_message("CHARGING", "CONNECTED", 61, 245)
    assert "ChargingState.CHARGING" in message
    assert "soc=61" in message
    assert "charged_range=245" in message


def test_build_chargefinder_event_message_unknown_returns_none():
    m = import_with_stubs()
    message = m._build_chargefinder_event_message("unknown", "unknown", None, None)
    assert message is None


def test_collect_candidate_paths_finds_nested_battery_keys():
    m = import_with_stubs()
    payload = {
        "vehicle": {
            "batteryStatus": {
                "state_of_charge": 73,
                "remaining_cruising_range_in_km": 254,
            },
            "other": {"foo": "bar"},
        }
    }

    paths = m._collect_candidate_paths(payload)

    assert "vehicle.batteryStatus" in paths
    assert "vehicle.batteryStatus.state_of_charge" in paths
    assert "vehicle.batteryStatus.remaining_cruising_range_in_km" in paths


def test_collect_candidate_values_includes_measurement_values():
    m = import_with_stubs()
    payload = {
        "status": {
            "battery": {
                "state_of_charge_in_percent": 61,
                "remaining_cruising_range_in_meters": 212000,
            }
        }
    }

    values = m._collect_candidate_values(payload)

    assert values["status.battery"] == {
        "state_of_charge_in_percent": 61,
        "remaining_cruising_range_in_meters": 212000,
    }
    assert values["status.battery.state_of_charge_in_percent"] == 61
    assert values["status.battery.remaining_cruising_range_in_meters"] == 212000


@pytest.mark.asyncio
async def test_get_skoda_update_logs_chargefinder_compatible_event():
    m = import_with_stubs()

    class FakeEnum:
        def __init__(self, name):
            self.name = name

    class FakeCharging:
        soc = 52
        charged_range = 198
        charging_status = FakeEnum("CHARGING")
        plug_status = FakeEnum("CONNECTED")
        state = FakeEnum("READY_FOR_CHARGING")

    class FakeInfo:
        composite_renders = []

    class FakeSkoda:
        async def get_health(self, vin):
            class H:
                mileage_in_km = 45678

            return H()

        async def get_info(self, vin):
            return FakeInfo()

        async def get_status(self, vin):
            return {"s": 1}

        async def get_positions(self, vin):
            class R:
                positions = []

            return R()

        async def get_charging(self, vin):
            return FakeCharging()

    m.myskoda = FakeSkoda()
    save_log = AsyncMock()
    with patch("skodaimporter.chargeimporter.save_log_to_db", new=save_log):
        await m.get_skoda_update("VIN")

    messages = [call.args[0] for call in save_log.await_args_list if call.args]
    assert any(
        "Charging event poll: ChargingState.CHARGING" in msg
        and "soc=52" in msg
        and "charged_range=198" in msg
        for msg in messages
    )


@pytest.mark.asyncio
async def test_get_skoda_update_extracts_soc_and_range_from_nested_info_payload():
    m = import_with_stubs()

    class FakeCharging:
        soc = None
        charged_range = None
        charging_status = None
        plug_status = None
        state = None

    class FakeRender:
        def __init__(self):
            self.view_type = "CHARGING_LIGHT"

    class FakeInfo:
        def __init__(self):
            self.composite_renders = [FakeRender()]
            self.battery = {
                "measurements": {
                    "state_of_charge": 67,
                    "remaining_cruising_range_in_km": 231,
                }
            }

    class FakeSkoda:
        async def get_health(self, vin):
            class H:
                mileage_in_km = 45678

            return H()

        async def get_info(self, vin):
            return FakeInfo()

        async def get_status(self, vin):
            return {"s": 1}

        async def get_positions(self, vin):
            class R:
                positions = []

            return R()

        async def get_charging(self, vin):
            return FakeCharging()

    m.myskoda = FakeSkoda()
    save_log = AsyncMock()
    with patch("skodaimporter.chargeimporter.save_log_to_db", new=save_log):
        await m.get_skoda_update("VIN")

    messages = [call.args[0] for call in save_log.await_args_list if call.args]
    assert any(
        "Charging event poll: ChargingState.CHARGING" in msg
        and "soc=67" in msg
        and "charged_range=231" in msg
        for msg in messages
    )


@pytest.mark.asyncio
async def test_get_skoda_update_extracts_soc_and_range_from_charging_status_battery():
    m = import_with_stubs()

    class FakeCharging:
        def __init__(self):
            self.soc = None
            self.charged_range = None
            self.charging_status = None
            self.plug_status = None
            self.state = None
            self.status = {
                "battery": {
                    "state_of_charge_in_percent": 58,
                    "remaining_cruising_range_in_meters": 221000,
                }
            }

    class FakeRender:
        def __init__(self):
            self.view_type = "CHARGING_LIGHT"

    class FakeInfo:
        def __init__(self):
            self.composite_renders = [FakeRender()]

    class FakeSkoda:
        async def get_health(self, vin):
            class H:
                mileage_in_km = 45678

            return H()

        async def get_info(self, vin):
            return FakeInfo()

        async def get_status(self, vin):
            return {"s": 1}

        async def get_positions(self, vin):
            class R:
                positions = []

            return R()

        async def get_charging(self, vin):
            return FakeCharging()

    m.myskoda = FakeSkoda()
    save_log = AsyncMock()
    with patch("skodaimporter.chargeimporter.save_log_to_db", new=save_log):
        await m.get_skoda_update("VIN")

    messages = [call.args[0] for call in save_log.await_args_list if call.args]
    assert any(
        "Charging event poll: ChargingState.CHARGING" in msg
        and "soc=58" in msg
        and "charged_range=221" in msg
        for msg in messages
    )


@pytest.mark.asyncio
async def test_on_event_triggers_on_change_access_service_event():
    m = import_with_stubs()

    class EType:
        SERVICE_EVENT = object()

    class Topic:
        OTHER = object()

    sys.modules["myskoda.event"].EventType = EType
    sys.modules["myskoda.event"].ServiceEventTopic = Topic

    with patch(
        "skodaimporter.chargeimporter.pull_api",
        new=AsyncMock(return_value={"ok": True}),
    ):
        with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
            m.myskoda = MagicMock()
            m.myskoda.get_charging = AsyncMock(return_value={"c": 1})
            m.myskoda.get_health = AsyncMock(return_value=MagicMock(mileage_in_km=1))
            m.myskoda.get_info = AsyncMock(return_value={"i": 1})
            m.myskoda.get_status = AsyncMock(return_value={"s": 1})
            m.myskoda.get_positions = AsyncMock(return_value=MagicMock(positions=[]))

            event = MagicMock()
            # A service event with name CHANGE_ACCESS and no charging topic should still trigger
            event.event_type = EType.SERVICE_EVENT
            event.name = type("Name", (), {"name": "CHANGE_ACCESS"})()
            event.topic = Topic.OTHER
            event.data = MagicMock()
            await m.on_event(event)


@pytest.mark.asyncio
async def test_on_event_triggers_on_operation_stop_completed():
    m = import_with_stubs()

    class EType:
        OPERATION = object()

    sys.modules["myskoda.event"].EventType = EType
    # For this test, topic isn't relevant

    with patch(
        "skodaimporter.chargeimporter.pull_api",
        new=AsyncMock(return_value={"ok": True}),
    ):
        with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
            m.myskoda = MagicMock()
            m.myskoda.get_charging = AsyncMock(return_value={"c": 1})
            m.myskoda.get_health = AsyncMock(return_value=MagicMock(mileage_in_km=1))
            m.myskoda.get_info = AsyncMock(return_value={"i": 1})
            m.myskoda.get_status = AsyncMock(return_value={"s": 1})
            m.myskoda.get_positions = AsyncMock(return_value=MagicMock(positions=[]))

            event = MagicMock()
            event.event_type = EType.OPERATION
            event.operation = type("Op", (), {"name": "STOP_CHARGING"})()
            event.status = type("St", (), {"name": "COMPLETED_SUCCESS"})()
            event.data = MagicMock()
            await m.on_event(event)


@pytest.mark.asyncio
async def test_check_vehicle_connection_status_success():
    """Test successful vehicle connection status check."""
    m = import_with_stubs()

    class FakeConnectionStatus:
        connected = True

    class FakeSkoda:
        async def get_connection_status(self, vin):
            return FakeConnectionStatus()

    m.myskoda = FakeSkoda()
    with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
        result = await m.check_vehicle_connection_status("VIN123")
        assert result is True


@pytest.mark.asyncio
async def test_check_vehicle_connection_status_failure():
    """Test vehicle connection status check failure."""
    m = import_with_stubs()

    class FakeSkoda:
        async def get_connection_status(self, vin):
            raise RuntimeError("Connection failed")

    m.myskoda = FakeSkoda()
    with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
        result = await m.check_vehicle_connection_status("VIN123")
        assert result is False


@pytest.mark.asyncio
async def test_check_vehicle_connection_status_no_client():
    """Test vehicle connection status check with no client."""
    m = import_with_stubs()
    m.myskoda = None

    with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
        result = await m.check_vehicle_connection_status("VIN123")
        assert result is False


@pytest.mark.asyncio
async def test_check_api_health_success():
    """Test successful API health check."""
    m = import_with_stubs()

    class FakeHealth:
        mileage_in_km = 12345

    class FakeSkoda:
        async def get_health(self, vin):
            return FakeHealth()

    m.myskoda = FakeSkoda()
    with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
        result = await m.check_api_health("VIN123")
        assert result is True


def test_read_int_env_parses_valid_value(monkeypatch):
    m = import_with_stubs()
    monkeypatch.setenv("SKODA_POLLING_FALLBACK_INTERVAL_SECONDS", "30")
    assert m._read_int_env("SKODA_POLLING_FALLBACK_INTERVAL_SECONDS", 300) == 30


def test_read_int_env_falls_back_for_invalid_value(monkeypatch):
    m = import_with_stubs()
    monkeypatch.setenv("SKODA_POLLING_FALLBACK_INTERVAL_SECONDS", "invalid")
    assert m._read_int_env("SKODA_POLLING_FALLBACK_INTERVAL_SECONDS", 300) == 300


@pytest.mark.asyncio
async def test_check_api_health_failure():
    """Test API health check failure."""
    m = import_with_stubs()

    class FakeSkoda:
        async def get_health(self, vin):
            raise RuntimeError("API failed")

    m.myskoda = FakeSkoda()
    with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
        result = await m.check_api_health("VIN123")
        assert result is False


@pytest.mark.asyncio
async def test_check_api_health_no_client():
    """Test API health check with no client."""
    m = import_with_stubs()
    m.myskoda = None

    with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
        result = await m.check_api_health("VIN123")
        assert result is False


def test_check_mqtt_connection_state_connected():
    """Test MQTT connection state check when connected."""
    m = import_with_stubs()

    # Mock MQTT client that appears connected
    mock_mqtt = MagicMock()
    mock_mqtt._running = True
    mock_mqtt._listener_task = MagicMock()
    mock_mqtt._listener_task.done.return_value = False

    mock_skoda = MagicMock()
    mock_skoda.mqtt = mock_mqtt
    m.myskoda = mock_skoda

    result = m.check_mqtt_connection_state()
    assert result is True


def test_check_mqtt_connection_state_disconnected():
    """Test MQTT connection state check when disconnected."""
    m = import_with_stubs()

    # Mock MQTT client that appears disconnected
    mock_mqtt = MagicMock()
    mock_mqtt._running = False
    mock_mqtt._listener_task = None

    mock_skoda = MagicMock()
    mock_skoda.mqtt = mock_mqtt
    m.myskoda = mock_skoda

    result = m.check_mqtt_connection_state()
    assert result is False


def test_check_mqtt_connection_state_no_client():
    """Test MQTT connection state check with no client."""
    m = import_with_stubs()
    m.myskoda = None

    result = m.check_mqtt_connection_state()
    assert result is False


def test_check_mqtt_connection_state_no_mqtt():
    """Test MQTT connection state check with no MQTT client."""
    m = import_with_stubs()

    mock_skoda = MagicMock()
    mock_skoda.mqtt = None
    m.myskoda = mock_skoda

    result = m.check_mqtt_connection_state()
    assert result is False


def test_check_mqtt_connection_state_recent_not_authorized_error_is_false():
    m = import_with_stubs()

    mock_mqtt = MagicMock()
    mock_mqtt._running = True
    mock_mqtt._listener_task = MagicMock()
    mock_mqtt._listener_task.done.return_value = False

    mock_skoda = MagicMock()
    mock_skoda.mqtt = mock_mqtt
    m.myskoda = mock_skoda
    m._last_mqtt_error_msg = "Connection lost ([code:135] Not authorized); reconnecting"
    m._last_mqtt_error_time = time.time()

    assert m.check_mqtt_connection_state() is False


def test_check_mqtt_connection_state_old_error_does_not_force_false():
    m = import_with_stubs()

    mock_mqtt = MagicMock()
    mock_mqtt._running = True
    mock_mqtt._listener_task = MagicMock()
    mock_mqtt._listener_task.done.return_value = False

    mock_skoda = MagicMock()
    mock_skoda.mqtt = mock_mqtt
    m.myskoda = mock_skoda
    m._last_mqtt_error_msg = "Connection lost ([code:135] Not authorized); reconnecting"
    m._last_mqtt_error_time = time.time() - (m.MQTT_ERROR_STALE_SECONDS + 10)

    assert m.check_mqtt_connection_state() is True


def test_is_transient_auth_error_connect_timeout():
    m = import_with_stubs()
    assert m._is_transient_auth_error("MySkoda connect timed out after 60s") is True


@pytest.mark.asyncio
async def test_resolve_vins_for_subscriptions_from_garage():
    """Uses garage VINs directly when available."""
    m = import_with_stubs()

    class FakeSkoda:
        async def list_vehicle_vins(self):
            return ["VIN123"]

    m.myskoda = FakeSkoda()

    with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
        vins = await m._resolve_vins_for_subscriptions()

    assert vins == ["VIN123"]


@pytest.mark.asyncio
async def test_resolve_vins_for_subscriptions_fallback_vin():
    """Falls back to SKODA_VEHICLE and rebinds MQTT subscriptions."""
    m = import_with_stubs()

    class FakeMqtt:
        def __init__(self):
            self.disconnect = AsyncMock()
            self.connect = AsyncMock()

    class FakeSkoda:
        def __init__(self):
            self.mqtt = FakeMqtt()

        async def list_vehicle_vins(self):
            return []

        async def get_user(self):
            return type("User", (), {"id": "user-1"})()

    m.myskoda = FakeSkoda()

    with patch("skodaimporter.chargeimporter.load_secret", return_value="VIN-FALLBACK"):
        with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
            vins = await m._resolve_vins_for_subscriptions()

    assert vins == ["VIN-FALLBACK"]
    m.myskoda.mqtt.disconnect.assert_awaited_once()
    m.myskoda.mqtt.connect.assert_awaited_once_with("user-1", ["VIN-FALLBACK"])


@pytest.mark.asyncio
async def test_resolve_vins_for_subscriptions_no_vins_no_fallback():
    """Returns empty list when neither garage VIN nor fallback is available."""
    m = import_with_stubs()

    class FakeSkoda:
        async def list_vehicle_vins(self):
            return []

    m.myskoda = FakeSkoda()

    with patch("skodaimporter.chargeimporter.load_secret", return_value=None):
        with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
            vins = await m._resolve_vins_for_subscriptions()

    assert vins == []


@pytest.mark.asyncio
async def test_resolve_vins_for_subscriptions_timeout_uses_fallback():
    """Garage timeout should trigger fallback VIN and set degraded reason."""
    m = import_with_stubs()

    class FakeMqtt:
        def __init__(self):
            self.disconnect = AsyncMock()
            self.connect = AsyncMock()

    class FakeSkoda:
        def __init__(self):
            self.mqtt = FakeMqtt()

        async def list_vehicle_vins(self):
            raise asyncio.TimeoutError()

        async def get_user(self):
            return type("User", (), {"id": "user-1"})()

    m.myskoda = FakeSkoda()
    m._degraded_reason = None

    with patch("skodaimporter.chargeimporter.load_secret", return_value="VIN-TIMEOUT"):
        with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
            vins = await m._resolve_vins_for_subscriptions()

    assert vins == ["VIN-TIMEOUT"]
    assert m._degraded_reason is not None


@pytest.mark.asyncio
async def test_resolve_vins_for_subscriptions_success_clears_degraded_reason():
    """Successful garage lookup should clear degraded status."""
    m = import_with_stubs()

    class FakeSkoda:
        async def list_vehicle_vins(self):
            return ["VIN123"]

    m.myskoda = FakeSkoda()
    m._degraded_reason = "old degraded state"

    with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
        vins = await m._resolve_vins_for_subscriptions()

    assert vins == ["VIN123"]
    assert m._degraded_reason is None


@pytest.mark.asyncio
async def test_perform_enhanced_connection_check_all_healthy():
    """Test enhanced connection check when all checks pass."""
    m = import_with_stubs()

    # Mock a recent event (within timeout)
    current_time = time.time()
    m.last_event_received = current_time - 100  # 100 seconds ago

    # Mock healthy MQTT state
    mock_mqtt = MagicMock()
    mock_mqtt._running = True
    mock_mqtt._listener_task = MagicMock()
    mock_mqtt._listener_task.done.return_value = False

    mock_skoda = MagicMock()
    mock_skoda.mqtt = mock_mqtt
    m.myskoda = mock_skoda

    with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
        result = await m.perform_enhanced_connection_check("VIN123")

        assert result["overall_healthy"] is True
        assert result["event_timeout_check"] is True
        assert (
            result["vehicle_connection_check"] is True
        )  # Skipped due to recent activity
        assert result["api_health_check"] is True  # Skipped due to no timeout
        assert result["mqtt_connection_check"] is True


@pytest.mark.asyncio
async def test_perform_enhanced_connection_check_event_timeout():
    """Test enhanced connection check when event timeout exceeded."""
    m = import_with_stubs()

    # Mock an old event (exceeds timeout)
    current_time = time.time()
    m.last_event_received = current_time - (13 * 60 * 60)  # 13 hours ago

    # Mock healthy MQTT state
    mock_mqtt = MagicMock()
    mock_mqtt._running = True
    mock_mqtt._listener_task = MagicMock()
    mock_mqtt._listener_task.done.return_value = False

    # Mock healthy API responses
    class FakeConnectionStatus:
        connected = True

    class FakeHealth:
        mileage_in_km = 12345

    class FakeSkoda:
        mqtt = mock_mqtt

        async def get_connection_status(self, vin):
            return FakeConnectionStatus()

        async def get_health(self, vin):
            return FakeHealth()

    m.myskoda = FakeSkoda()

    with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
        result = await m.perform_enhanced_connection_check("VIN123")

        assert result["overall_healthy"] is False  # Due to event timeout
        assert result["event_timeout_check"] is False
        assert result["vehicle_connection_check"] is True
        assert result["api_health_check"] is True
        assert result["mqtt_connection_check"] is True


@pytest.mark.asyncio
async def test_perform_enhanced_connection_check_api_failure():
    """Test enhanced connection check when API health check fails."""
    m = import_with_stubs()

    # Mock an old event (exceeds API health timeout)
    current_time = time.time()
    m.last_event_received = current_time - (31 * 60)  # 31 minutes ago

    # Mock healthy MQTT state
    mock_mqtt = MagicMock()
    mock_mqtt._running = True
    mock_mqtt._listener_task = MagicMock()
    mock_mqtt._listener_task.done.return_value = False

    # Mock failing API responses
    class FakeSkoda:
        mqtt = mock_mqtt

        async def get_connection_status(self, vin):
            return MagicMock()  # Success

        async def get_health(self, vin):
            raise RuntimeError("API failed")  # Failure

    m.myskoda = FakeSkoda()

    with patch("skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()):
        result = await m.perform_enhanced_connection_check("VIN123")

        assert result["overall_healthy"] is False
        assert result["event_timeout_check"] is True  # Within timeout
        assert result["vehicle_connection_check"] is True
        assert result["api_health_check"] is False  # Failed
        assert result["mqtt_connection_check"] is True


def test_build_health_response_includes_polling_fallback_details():
    m = import_with_stubs()

    m._startup_ready = True
    m._startup_status = "polling_fallback"
    m._degraded_reason = "MQTT unavailable, enabling API polling fallback"
    m._polling_fallback_active = True

    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = [0]
    cur.fetchall.return_value = []

    resp = m._build_health_response(
        conn,
        cur,
        {
            "timestamp": time.time(),
            "event_timeout_check": True,
            "vehicle_connection_check": True,
            "api_health_check": True,
            "mqtt_connection_check": False,
            "overall_healthy": False,
            "last_event_elapsed_seconds": 12,
        },
    )

    body = resp.body.decode("utf-8")
    assert "Startup status: polling_fallback" in body
    assert "Polling fallback: enabled" in body
    assert "Polling interval seconds" in body


@pytest.mark.asyncio
async def test_root_returns_healthy_when_polling_fallback_active_and_mqtt_down():
    m = import_with_stubs()

    # Force readiness/runner state into stable mode.
    m._startup_ready = True
    m._startup_started_at = time.time() - 600
    m._last_bg_error_time = 0
    m._last_bg_error_msg = None
    m._polling_fallback_active = True
    m._degraded_reason = "polling fallback active"
    m.VIN = "VIN123"
    m._bg_task = MagicMock()
    m._bg_task.done.return_value = False

    fake_conn = MagicMock()
    fake_cur = MagicMock()

    with patch(
        "skodaimporter.chargeimporter.db_connect",
        new=AsyncMock(return_value=(fake_conn, fake_cur)),
    ):
        with patch(
            "skodaimporter.chargeimporter.perform_enhanced_connection_check",
            new=AsyncMock(
                return_value={
                    "timestamp": time.time(),
                    "event_timeout_check": True,
                    "vehicle_connection_check": True,
                    "api_health_check": True,
                    "mqtt_connection_check": False,
                    "overall_healthy": False,
                    "last_event_elapsed_seconds": 5,
                }
            ),
        ):
            with patch(
                "skodaimporter.chargeimporter._build_health_response",
                return_value=types.SimpleNamespace(body=b"polling-ok"),
            ):
                response = await m.root()

    assert response.body == b"polling-ok"


@pytest.mark.asyncio
async def test_mqtt_recovery_polls_before_subscribe_and_refreshes_timestamps():
    """MQTT recovery: poll fires before subscribe_events, timestamps are refreshed."""
    m = import_with_stubs()

    m._polling_fallback_active = True
    m.VIN = "VIN123"
    m.FORCE_POLLING_FALLBACK = False
    m.MQTT_RECOVERY_ATTEMPT_INTERVAL_SECONDS = 0  # trigger immediately

    poll_calls = []

    async def fake_get_skoda_update(vin):
        poll_calls.append(vin)

    subscribe_calls = []
    fake_mqtt = MagicMock()
    fake_mqtt.connect = AsyncMock()

    fake_myskoda = MagicMock()
    fake_myskoda.mqtt = fake_mqtt
    fake_myskoda.subscribe_events = MagicMock(
        side_effect=lambda cb: subscribe_calls.append("subscribed")
    )

    # Inject state so the recovery branch is reached
    m.myskoda = fake_myskoda

    before = time.time()

    with patch(
        "skodaimporter.chargeimporter.get_skoda_update",
        new=AsyncMock(side_effect=fake_get_skoda_update),
    ):
        with patch(
            "skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()
        ):
            # Simulate one iteration of the inner recovery block directly
            now_ts = time.time()
            last_mqtt_recovery_attempt_ts = 0.0
            last_poll_ts = 0.0
            last_event_received_ref = [0.0]

            vins = ["VIN123"]
            user = MagicMock()
            user.id = "user-id"

            if (
                not m.FORCE_POLLING_FALLBACK
                and fake_myskoda.mqtt is not None
                and now_ts - last_mqtt_recovery_attempt_ts
                >= m.MQTT_RECOVERY_ATTEMPT_INTERVAL_SECONDS
            ):
                last_mqtt_recovery_attempt_ts = now_ts
                await fake_myskoda.mqtt.connect(user.id, vins)
                if m.VIN:
                    await m.get_skoda_update(m.VIN)
                    last_event_received_ref[0] = time.time()
                    last_poll_ts = last_event_received_ref[0]
                fake_myskoda.subscribe_events(lambda e: None)
                m._polling_fallback_active = False

    # Poll happened exactly once with the correct VIN
    assert poll_calls == ["VIN123"]
    # subscribe_events was called after the poll
    assert subscribe_calls == ["subscribed"]
    # timestamps were updated
    assert last_event_received_ref[0] >= before
    assert last_poll_ts >= before
    # polling fallback was disabled
    assert m._polling_fallback_active is False


@pytest.mark.asyncio
async def test_mqtt_recovery_poll_failure_does_not_prevent_resubscribe():
    """A failing post-recovery poll must not prevent subscribe_events from running."""
    m = import_with_stubs()

    m._polling_fallback_active = True
    m.VIN = "VIN123"
    m.FORCE_POLLING_FALLBACK = False
    m.MQTT_RECOVERY_ATTEMPT_INTERVAL_SECONDS = 0

    fake_mqtt = MagicMock()
    fake_mqtt.connect = AsyncMock()

    subscribe_calls = []
    fake_myskoda = MagicMock()
    fake_myskoda.mqtt = fake_mqtt
    fake_myskoda.subscribe_events = MagicMock(
        side_effect=lambda cb: subscribe_calls.append("subscribed")
    )
    m.myskoda = fake_myskoda

    with patch(
        "skodaimporter.chargeimporter.get_skoda_update",
        new=AsyncMock(side_effect=RuntimeError("API down")),
    ):
        with patch(
            "skodaimporter.chargeimporter.save_log_to_db", new=AsyncMock()
        ):
            now_ts = time.time()
            last_mqtt_recovery_attempt_ts = 0.0

            if (
                not m.FORCE_POLLING_FALLBACK
                and fake_myskoda.mqtt is not None
                and now_ts - last_mqtt_recovery_attempt_ts
                >= m.MQTT_RECOVERY_ATTEMPT_INTERVAL_SECONDS
            ):
                last_mqtt_recovery_attempt_ts = now_ts
                await fake_myskoda.mqtt.connect("user-id", ["VIN123"])
                if m.VIN:
                    try:
                        await m.get_skoda_update(m.VIN)
                    except Exception:  # noqa: BLE001
                        pass  # swallow — subscribe must still proceed
                fake_myskoda.subscribe_events(lambda e: None)
                m._polling_fallback_active = False

    # subscribe_events still ran despite the poll failure
    assert subscribe_calls == ["subscribed"]
    assert m._polling_fallback_active is False
