import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch
import time

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
        assert result["vehicle_connection_check"] is True  # Skipped due to recent activity
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
