import asyncio
import datetime
import json
import logging
import os
import time

import mariadb
from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from helpers import (
    compute_daily_totals_home,
    compute_session_summary,
    group_sessions_by_mileage,
)

from commons import db_connect, get_logger, load_secret

lastsoc = 0
lastrange = 0
lastlat = 0
lastlon = 0
my_logger = get_logger("skodachargefrontendlogger")
my_logger.warning("Starting the application...")


async def ordinal(n):
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    else:
        return f"{n}{['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]}"


app = FastAPI()


@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root(
    year: int = Query(datetime.datetime.now().year, ge=2025, le=2027),
    month: int = Query(datetime.datetime.now().month, ge=1, le=12),
):
    conn, cur = await db_connect(my_logger)
    start_date = datetime.date(year, month, 1)
    if month == 12:
        end_date = datetime.date(year + 1, 1, 1)
    else:
        end_date = datetime.date(year, month + 1, 1)
    # Fetch raw hourly rows; we'll group into sessions (plug-in -> unplug) in Python
    query = (
        "SELECT log_timestamp, start_at, stop_at, amount, price, charged_range, "
        "start_range, mileage, position, soc "
        "FROM skoda.charge_hours "
        "WHERE stop_at >= %s AND stop_at < %s "
        "ORDER BY mileage, start_at, log_timestamp"
    )
    my_logger.debug(
        "Executing session source query with start_date: %s, end_date: %s",
        start_date,
        end_date,
    )
    cur.execute(query, (start_date, end_date))
    rows = cur.fetchall() or []

    # Normalize to tuples of python types
    def _to_dt(v):
        if v is None:
            return None
        if isinstance(v, datetime.datetime):
            return v
        try:
            return datetime.datetime.fromisoformat(str(v))
        except Exception:
            return None

    hourly = [
        {
            "log_timestamp": _to_dt(r[0]),
            "start_at": _to_dt(r[1]),
            "stop_at": _to_dt(r[2]),
            "amount": float(r[3]) if r[3] is not None else 0.0,
            "price": float(r[4]) if r[4] is not None else 0.0,
            "charged_range": float(r[5]) if r[5] is not None else None,
            "start_range": float(r[6]) if r[6] is not None else None,
            "mileage": r[7],
            "position": r[8] or "Unknown",
            "soc": float(r[9]) if r[9] is not None else None,
        }
        for r in rows
    ]

    # Group hourly rows to sessions by mileage
    sessions = group_sessions_by_mileage(hourly)

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
    if not sessions:
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Charge Summary for {year}-{month:02d}</title>
            <link href="https://unpkg.com/tailwindcss@^2/dist/tailwind.min.css" rel="stylesheet">
        </head>
        <body class="bg-black">
            <section class="bg-black">
                <div class="container px-5 py-12 mx-auto lg:px-20">
                    <div class="flex flex-col flex-wrap pb-6 mb-12 text-white">
                        <h1 class="mb-12 text-3xl font-medium text-white">
                            Charge Summary for {year}-{month:02d}
                        </h1>
                        <p class="text-white text-xl">No charge data found for this month.</p>
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
    total_amount = 0.0
    total_price = 0.0
    total_range_per_kwh = 0.0
    range_count = 0
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
                <div class="flex flex-col flex-wrap text-white">
                    <h1 class="mb-12 text-3xl font-medium text-white">
                        Charge Summary for {year}-{month:02d}
                    </h1>
                </div>
                <!-- Daily totals table -->
                <div class="mb-8">
                    <div class="divTable">
                        <div class="divTableHeading">
                            <div class="divTableRow">
                                <div class="divTableHead">Day</div>
                                <div class="divTableHead">Total kWh</div>
                                <div class="divTableHead">Total DKK</div>
                            </div>
                        </div>
                        <div class="divTableBody">
    """
    # Compute daily totals for home-only rows
    daily = compute_daily_totals_home(hourly)

    for d in sorted(daily.keys()):
        kwh = round(daily[d]["kwh"], 2)
        dkk = round(daily[d]["dkk"], 2)
        html += f"""
                            <div class=\"divTableRow\">
                                <div class=\"divTableCell text-white\">{d.strftime('%Y-%m-%d')}</div>
                                <div class=\"divTableCell text-white\">{kwh:.2f} kWh</div>
                                <div class=\"divTableCell text-white\">{dkk:.2f} DKK</div>
                            </div>
        """
    html += """
                        </div>
                    </div>
                </div>
                <!-- Sessions table -->
                <div class="divTable">
                    <div class="divTableHeading">
                        <div class="divTableRow">
                            <div class="divTableHead">Charge ended at</div>
                            <div class="divTableHead">KM</div>
                            <div class="divTableHead">Charge kWh</div>
                            <div class="divTableHead">Price (DKK)</div>
                            <div class="divTableHead">Range @ 100%</div>
                            <div class="divTableHead">Added range</div>
                            <div class="divTableHead">km pr kWh</div>
                            <div class="divTableHead">SOC</div>
                            <div class="divTableHead">Position</div>
                        </div>
                    </div>
                    <div class="divTableBody">
    """
    # We'll compute totalmileage for the footer after iterating sessions
    totalmileage = 0

    for sess in sessions:
        sess_rows = sess["rows"]
        mileage = sess["mileage"]
        stopped_at = sess["end"]
        # Session summary with away policy
        summary = compute_session_summary(sess_rows)
        amount = summary["amount"]
        price = summary["price"]
        any_away = summary["any_away"]
        position = summary["position"]
        # Range aggregation
        charged_range_vals = [
            r["charged_range"] for r in sess_rows if r["charged_range"] is not None
        ]
        start_range_vals = [
            r["start_range"] for r in sess_rows if r["start_range"] is not None
        ]
        charged_range = round(charged_range_vals[-1], 2) if charged_range_vals else 0
        range_diff = 0
        if charged_range_vals and start_range_vals:
            range_diff = max(charged_range_vals) - min(start_range_vals)
        soc_vals = [r["soc"] for r in sess_rows if r["soc"] is not None]
        soc = soc_vals[-1] if soc_vals else None
        range_per_kwh = (
            round(range_diff / amount, 2) if amount > 0 and range_diff > 0 else 0
        )
        if range_per_kwh > 0:
            total_range_per_kwh = total_range_per_kwh + range_per_kwh
            range_count += 1
        total_amount += amount
        total_price += price
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
                        <div class=\"divTableRow\">\n
                            <div class=\"divTableCell text-white\">{stopped_at_str}</div>\n
                            <div class=\"divTableCell text-white\">{mileage}</div>\n
                            <div class=\"divTableCell text-white\">{amount:.2f} kWh</div>\n
                            <div class=\"divTableCell text-white\">{price:.2f} DKK</div>\n
                            <div class=\"divTableCell text-white\">{int(charged_range / soc * 100) if charged_range and soc else 0} KM</div>\n
                            <div class=\"divTableCell text-white\">{range_diff if range_diff > 0 else 0} KM</div>\n
                            <div class=\"divTableCell text-white\">{range_per_kwh}</div>\n
                            <div class=\"divTableCell text-white\">{int(soc) if soc is not None else 0}%</div>\n
                            <div class=\"divTableCell text-white\">{position}</div>\n
                        </div>
        """
    # Compute month-wide mileage change across sessions for footer
    miles = [s["mileage"] for s in sessions if s.get("mileage") is not None]
    if miles:
        totalmileage = max(miles) - min(miles)
    avg_range_per_kwh = round(totalmileage / total_amount, 2) if total_amount > 0 else 0
    html += f"""
                    </div>
            <div class="divTableFoot">
                        <div class="divTableRow font-bold">
                <div class="divTableCell">{len(sessions)} charges</div>
                            <div class="divTableCell">{totalmileage} KM</div>
                            <div class="divTableCell">{total_amount:.2f} kWh</div>
                            <div class="divTableCell">{total_price:.2f} DKK</div>
                            <div class="divTableCell"></div>
                            <div class="divTableCell"></div>
                            <div class="divTableCell">Estimated: {round(total_range_per_kwh / range_count, 2) if total_range_per_kwh > 0 and range_count > 0 else 0}
                            <br />Actual: {avg_range_per_kwh if total_range_per_kwh > 0 and range_count > 0 else 0}</div>
                            <div class="divTableCell"></div>
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
