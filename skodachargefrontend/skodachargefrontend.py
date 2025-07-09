import asyncio
import datetime
import json
import logging
import os
import time

import mariadb
from commons import load_secret
from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

lastsoc = 0
lastrange = 0
lastlat = 0
lastlon = 0

my_logger = logging.getLogger("skodachargefindlogger")
my_logger.setLevel(logging.DEBUG)

file_handler = logging.FileHandler("app.log")
file_handler.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)

# Optional: set a formatter
formatter = logging.Formatter("%(funcName)s - %(lineno)d - %(message)s")
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# Add the handler to the logger
my_logger.addHandler(file_handler)
my_logger.addHandler(console_handler)

my_logger.warning("Starting the application...")

try:
    my_logger.debug("Connecting to MariaDB...")
    conn = mariadb.connect(
        user=load_secret("MARIADB_USERNAME"),
        password=load_secret("MARIADB_PASSWORD"),
        host=load_secret("MARIADB_HOSTNAME"),
        port=3306,
        database=load_secret("MARIADB_DATABASE"),
    )
    conn.auto_reconnect = True
    my_logger.debug("Connected to MariaDB")

except mariadb.Error as e:
    my_logger.error(f"Error connecting to MariaDB Platform: {e}")
    print(f"Error connecting to MariaDB Platform: {e}")
    import os
    import signal

    os.kill(os.getpid(), signal.SIGINT)

cur = conn.cursor()


async def ordinal(n):
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    else:
        return f"{n}{['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]}"


app = FastAPI()


@app.get("/", response_class=HTMLResponse)
async def root(
    year: int = Query(datetime.datetime.now().year, ge=2025, le=2027),
    month: int = Query(datetime.datetime.now().month, ge=1, le=12),
):
    # Calculate first and last day of the month
    start_date = datetime.date(year, month, 1)
    if month == 12:
        end_date = datetime.date(year + 1, 1, 1)
    else:
        end_date = datetime.date(year, month + 1, 1)

    query = """
        SELECT
            SUM(amount) AS amount,
            SUM(price) AS price,
            MAX(charged_range) AS charged_range,
            MAX(mileage) AS mileage,
            MAX(stop_at) AS stopped_at,
            position AS position,
            MAX(charged_range)-MIN(charged_range) AS charged_range_diff

        FROM skoda.charge_hours
        WHERE stop_at >= %s AND stop_at < %s
        GROUP BY mileage
        ORDER BY mileage
    """
    cur.execute(query, (start_date, end_date))
    rows = cur.fetchall()

    # Calculate totals
    total_amount = 0.0
    total_price = 0.0
    total_range_per_kwh = 0
    range_count = 0

    # Pagination logic
    prev_month = month - 1
    prev_year = year
    next_month = month + 1
    next_year = year
    if prev_month < 1:
        prev_month = 12
        prev_year -= 1
    if next_month > 12:
        next_month = 1
        next_year += 1

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Charge Summary for {year}-{month:02d}</title>
        <link href="https://unpkg.com/tailwindcss@^2/dist/tailwind.min.css" rel="stylesheet">
        <style>
            .divTable {{
                display: table;
                width: 100%;
            }}
            .divTableRow {{
                display: table-row;
            }}
            .divTableHeading {{
                background-color: #EEE;
                display: table-header-group;
            }}
            .divTableCell, .divTableHead {{
                border: 1px solid #999999;
                display: table-cell;
                padding: 3px 10px;
            }}
            .divTableHeading {{
                background-color: #EEE;
                display: table-header-group;
                font-weight: bold;
            }}
            .divTableFoot {{
                background-color: #EEE;
                display: table-footer-group;
                font-weight: bold;
            }}
            .divTableBody {{
                display: table-row-group;
            }}
        </style>
    </head>
    <body class="bg-black">
        <section class="bg-black">
            <div class="container px-5 py-12 mx-auto lg:px-20">
                <div class="flex flex-col flex-wrap pb-6 mb-12 text-white">
                    <h1 class="mb-12 text-3xl font-medium text-white">
                        Charge Summary for {year}-{month:02d}
                    </h1>
                </div>
                <div class="text-center mb-8">
                    <form method="get" class="inline-block">
                        <label for="year" class="text-white mr-2">Year:</label>
                        <input type="number" id="year" name="year" value="{year}" min="2000" max="2100" class="rounded px-2 py-1 mr-4">
                        <label for="month" class="text-white mr-2">Month:</label>
                        <input type="number" id="month" name="month" value="{month}" min="1" max="12" class="rounded px-2 py-1 mr-4">
                        <button type="submit" class="bg-blue-600 text-white px-4 py-1 rounded">Go</button>
                    </form>
                </div>
                <div class="divTable">
                    <div class="divTableHeading">
                        <div class="divTableRow">
                            <div class="divTableHead">Charge ended at</div>
                            <div class="divTableHead">KM</div>
                            <div class="divTableHead">Charge kWh</div>
                            <div class="divTableHead">Price (DKK)</div>
                            <div class="divTableHead">Range when done</div>
                            <div class="divTableHead">Charged range</div>
                            <div class="divTableHead">Estimated range per kWh</div>
                            <div class="divTableHead">Position</div>
                        </div>
                    </div>
                    <div class="divTableBody">
    """
    minmileage = min(row[3] for row in rows) if rows else 0
    maxmileage = max(row[3] for row in rows) if rows else 0
    for row in rows:
        amount = round(row[0] or 0, 2)
        price = round(row[1] or 0, 2)
        charged_range = round(row[2] or 0, 2)
        mileage = row[3]
        stopped_at = row[4]
        position = row[5] if row[5] else "Unknown"
        range_diff = row[6] if row[6] else 0
        range_per_kwh = (
            round(range_diff / amount, 2) if (amount > 0 and range_diff > 0) else 0
        )
        if range_per_kwh > 0:
            total_range_per_kwh = total_range_per_kwh + range_per_kwh
            range_count += 1

        if position != "home":
            price = 0.0  # If not at home, we don't charge for the electricity
        total_amount += amount
        total_price += price

        # Format stopped_at to only show the hour (HH:MM)
        stopped_at_str = ""
        if stopped_at:
            try:
                if isinstance(stopped_at, str):
                    dt = datetime.datetime.fromisoformat(stopped_at)
                else:
                    dt = stopped_at
                day = await ordinal(dt.day)
                stopped_at_str = dt.strftime("%a ") + day + " @ " + dt.strftime("%H:%M")
            except Exception:
                stopped_at_str = str(stopped_at)
        else:
            stopped_at_str = ""

        html += f"""
                        <div class="divTableRow">
                            <div class="divTableCell text-white">{stopped_at_str}</div>
                            <div class="divTableCell text-white">{mileage}</div>
                            <div class="divTableCell text-white">{amount:.2f} kWh</div>
                            <div class="divTableCell text-white">{price:.2f} DKK</div>
                            <div class="divTableCell text-white">{charged_range} KM</div>
                            <div class="divTableCell text-white">{range_diff} KM</div>
                            <div class="divTableCell text-white">{range_per_kwh}</div>
                            <div class="divTableCell text-white">{position}</div>
                        </div>
        """
    avg_range_per_kwh = round((maxmileage - minmileage) / total_amount, 2)

    # Add totals row
    html += f"""
                    </div>
                    <div class="divTableFoot">
                        <div class="divTableRow font-bold">
                            <div class="divTableCell">{len(rows)} charges</div>
                            <div class="divTableCell">{maxmileage-minmileage} KM</div>
                            <div class="divTableCell">{total_amount:.2f} kWh</div>
                            <div class="divTableCell">{total_price:.2f} DKK</div>
                            <div class="divTableCell"></div>
                            <div class="divTableCell"></div>
                            <div class="divTableCell">Estimated: {round(total_range_per_kwh / range_count,2) if total_range_per_kwh > 0 and range_count > 0 else 0}<br />Actual: {avg_range_per_kwh  if total_range_per_kwh > 0 and range_count > 0 else 0 }</div>
                            <div class="divTableCell"></div>
                        </div>
                    </div>
                </div>
                <div class="text-center mt-8">
                    <a href="/?year={prev_year}&month={prev_month}" class="text-blue-400 hover:underline">&laquo; Previous Month</a>
                    <span class="mx-2 text-white">|</span>
                    <a href="/" class="text-blue-400 hover:underline">Home</a>
                    <span class="mx-2 text-white">|</span>
                    <a href="/?year={next_year}&month={next_month}" class="text-blue-400 hover:underline">Next Month &raquo;</a>
                </div>
            </div>
        </section>
    </body>
    </html>
    """
    return HTMLResponse(content=html)
