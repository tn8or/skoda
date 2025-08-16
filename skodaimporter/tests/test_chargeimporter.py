import importlib
import sys
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
