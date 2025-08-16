import asyncio
import json
import os
import time
import mariadb
from aiohttp import ClientSession
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from myskoda import MySkoda
from myskoda.event import Event, EventType, ServiceEventTopic
from myskoda.models.charging import Charging
from myskoda.models.health import Health
from myskoda.models.info import Info
from myskoda.models.position import PositionType
from myskoda.models.status import Status
from commons import CHARGEFINDER_URL, db_connect, get_logger, load_secret, pull_api
VIN = ''
my_logger = get_logger('skodaimporter')
my_logger.warning('Starting the application...')
last_event_timeout = 4 * 60 * 60
last_event_received = time.time()


async def save_log_to_db(log_message):
    conn, cur = await db_connect(my_logger)
    try:
        cur.execute(
            'INSERT INTO rawlogs (log_message, log_timestamp) VALUES (?, NOW())'
            , (log_message,))
        conn.commit()
    except mariadb.Error as e:
        my_logger.error('Error saving log to database: %s', e)
        conn.rollback()
        import os
        import signal
        os.kill(os.getpid(), signal.SIGINT)


async def on_event(event: Event):
    global last_event_received
    event_json = json.dumps(event, default=str)
    my_logger.debug(event_json)
    await save_log_to_db(event_json)
    print(event)
    last_event_received = time.time()
    if event.type == EventType.SERVICE_EVENT:
        api_result = pull_api(CHARGEFINDER_URL, my_logger)
        my_logger.debug('API result: %s', api_result)
        my_logger.debug('Received service event.')
        await save_log_to_db('Received service event.')
        if event.topic == ServiceEventTopic.CHARGING:
            my_logger.debug('Battery is %s%% charged.', event.event.data.soc)
            await save_log_to_db(f'Battery is {event.event.data.soc}% charged.'
                )
            await get_skoda_update(VIN)
            charging: Charging = await myskoda.get_charging(VIN)
            my_logger.debug('Charging data fetched.')
            await save_log_to_db(f'Charging data fetched: {charging}')
            my_logger.debug(charging)


async def get_skoda_update(vin):
    my_logger.debug('Fetching vehicle health...')
    await save_log_to_db('Fetching vehicle health...')
    health: Health = await myskoda.get_health(vin)
    my_logger.debug('Vehicle health fetched.')
    await save_log_to_db(
        f'Vehicle health fetched, mileage: {health.mileage_in_km}')
    my_logger.debug('Mileage: %s', health.mileage_in_km)
    info = await myskoda.get_info(vin)
    await save_log_to_db(f'Vehicle info fetched: {info}')
    my_logger.debug('Vehicle info fetched.')
    my_logger.debug(info)
    status: Status = await myskoda.get_status(vin)
    my_logger.debug('Vehicle status fetched.')
    my_logger.debug(status)
    await save_log_to_db(f'Vehicle status fetched: {status}')
    my_logger.debug('Vehicle status fetched.')
    my_logger.debug('looking for positions...')
    pos = next(pos for pos in (await myskoda.get_positions(vin)).positions if
        pos.type == PositionType.VEHICLE)
    my_logger.debug('lat: %s, lng: %s', pos.gps_coordinates.latitude, pos.
        gps_coordinates.longitude)
    my_logger.debug('Vehicle positions fetched.')
    await save_log_to_db(
        f'Vehicle positions fetched: lat: {pos.gps_coordinates.latitude}, lng: {pos.gps_coordinates.longitude}'
        )


async def skodarunner():
    my_logger.debug('Starting main function...')
    async with ClientSession() as session:
        my_logger.debug('Creating MySkoda instance...')
        global myskoda
        myskoda = MySkoda(session)
        await myskoda.connect(load_secret('SKODA_USER'), load_secret(
            'SKODA_PASS'))
        my_logger.debug('Connected to MySkoda')
        global VIN
        for vin in (await myskoda.list_vehicle_vins()):
            print(f'Vehicle VIN: {vin}')
            VIN = vin
            await get_skoda_update(VIN)
        my_logger.debug('Vehicle VIN: %s', vin)
        my_logger.debug('Subscribing to events...')
        myskoda.subscribe_events(on_event)
        my_logger.debug('Subscribed to events')
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print('Shutting down...')
        finally:
            await myskoda.disconnect()


def read_last_n_lines(filename, n):
    with open(filename, 'r') as file:
        lines = file.readlines()
        return lines[-n:]


app = FastAPI()


@app.get('/')
async def root():
    conn, cur = await db_connect(my_logger)
    global last_event_received
    global last_event_timeout
    elapsed = time.time() - last_event_received
    if elapsed > last_event_timeout:
        my_logger.error(
            'Last event more than 1 hours old, triggering charge update')
        charge_result = await myskoda.refresh_charging(VIN)
        if not charge_result:
            my_logger.error(
                'Failed to refresh charging data. Triggering restart')
            raise HTTPException(status_code=503, detail=
                'Service temporarily unavailable')
        else:
            my_logger.debug('Charging refreshed: %s', charge_result)
    else:
        my_logger.info('Last event received %s seconds ago, within timeout.',
            int(elapsed))
        last_25_lines = read_last_n_lines('app.log', 15)
        last_25_lines_joined = ''.join(last_25_lines)
        try:
            cur.execute('SELECT COUNT(*) FROM skoda.rawlogs')
            count = cur.fetchone()[0]
            last_25_lines_joined += f'\n\nTotal logs in database: {count}\n'
            cur.execute(
                'SELECT * FROM skoda.rawlogs order by log_timestamp desc limit 10'
                )
        except mariadb.Error as e:
            my_logger.error('Error fetching from database: %s', e)
            conn.rollback()
            import os
            import signal
            os.kill(os.getpid(), signal.SIGINT)
        for log_timestamp, log_message in cur:
            last_25_lines_joined += f'{log_timestamp} - {log_message}\n'
        return PlainTextResponse(last_25_lines_joined.encode('utf-8'))


background = asyncio.create_task(skodarunner())
