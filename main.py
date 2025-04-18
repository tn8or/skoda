import asyncio
import json
import logging

import graypy
from aiohttp import ClientSession
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import PlainTextResponse
from myskoda import MySkoda
from myskoda.event import Event, EventType, ServiceEventTopic
from myskoda.models.health import Health
from myskoda.models.info import Info
from myskoda.models.position import PositionType
from myskoda.models.status import Status

from commons import load_secret

VIN = ""
my_logger = logging.getLogger("skodaimportlogger")
my_logger.setLevel(logging.DEBUG)
handler = graypy.GELFUDPHandler(
    "192.168.50.2", 12201
)  # Replace 'localhost' with your Graylog server address
my_logger.addHandler(handler)

file_handler = logging.FileHandler("app.log")
file_handler.setLevel(logging.DEBUG)

# Optional: set a formatter
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)

# Add the handler to the logger
my_logger.addHandler(file_handler)

my_logger.warn("Starting the application...")


async def on_event(event: Event):
    # Convert the event to a JSON string
    event_json = json.dumps(event, default=str)
    my_logger.debug(event_json)
    print(event)
    if event.type == EventType.SERVICE_EVENT:
        my_logger.debug("Received service event.")
        if event.topic == ServiceEventTopic.CHARGING:
            my_logger.debug("Battery is %s%% charged.", event.event.data.soc)
            await get_skoda_update(VIN)


async def get_skoda_update(vin):
    my_logger.debug("Fetching vehicle health...")
    health: Health = await myskoda.get_health(vin)
    my_logger.debug("Vehicle health fetched.")
    my_logger.debug("Mileage: %s", health.mileage_in_km)
    info = await myskoda.get_info(vin)
    my_logger.debug("Vehicle info fetched.")
    my_logger.debug(info)
    status: Status = await myskoda.get_status(vin)
    my_logger.debug("Vehicle status fetched.")
    my_logger.debug(status)
    my_logger.debug("looking for positions...")
    pos = next(
        pos
        for pos in (await myskoda.get_positions(vin)).positions
        if pos.type == PositionType.VEHICLE
    )
    my_logger.debug(
        "lat: %s, lng: %s", pos.gps_coordinates.latitude, pos.gps_coordinates.longitude
    )
    my_logger.debug("Vehicle positions fetched.")


async def skodarunner():
    my_logger.debug("Starting main function...")

    async with ClientSession() as session:
        my_logger.debug("Creating MySkoda instance...")
        global myskoda
        myskoda = MySkoda(session)
        await myskoda.connect(load_secret("SKODA_USER"), load_secret("SKODA_PASS"))
        my_logger.debug("Connected to MySkoda")
        global VIN
        for vin in await myskoda.list_vehicle_vins():
            print(f"Vehicle VIN: {vin}")
            VIN = vin
            await get_skoda_update(VIN)
        my_logger.debug(f"Vehicle VIN: {vin}")
        my_logger.debug("Subscribing to events...")
        myskoda.subscribe_events(on_event)
        my_logger.debug("Subscribed to events")

        # Keep the script running to listen for events
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("Shutting down...")
        finally:
            await myskoda.disconnect()


def read_last_n_lines(filename, n):
    with open(filename, "r") as file:
        lines = file.readlines()
        return lines[-n:]


app = FastAPI()


@app.get("/")
async def root():
    last_25_lines = read_last_n_lines("app.log", 30)
    last_25_lines_joined = "".join(last_25_lines)
    return PlainTextResponse(last_25_lines_joined.encode("utf-8"))


background = asyncio.create_task(skodarunner())
