import datetime
import html
import os
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from helpers import (
    compute_daily_totals_home,
    compute_session_summary,
    group_sessions_by_mileage,
)

from commons import db_connect, get_logger

my_logger = get_logger("skodachargefrontendlogger")
my_logger.warning("Starting the application...")

# Local timezone for all displayed timestamps
TZ_CPH = ZoneInfo("Europe/Copenhagen")

# Rawlog patterns that indicate the car actually sent telemetry.
# These match the log messages written by skodaimporter when it receives vehicle events.
VEHICLE_LOG_LIKE_PATTERNS = (
    "%ServiceEvent%",
    "%Charging event detected%",
    "%Charging data fetched%",
    "%ChargingState.%",
    "%Vehicle health fetched%",
    "%Vehicle info fetched%",
    "%Vehicle status fetched%",
    "%Vehicle positions fetched%",
    "%Vehicle position found%",
)


async def ordinal(n):
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    else:
        return f"{n}{['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]}"


def escape_html(value):
    """
    Escape HTML content to prevent XSS attacks.

    Args:
        value: The value to escape. Can be string, int, float, or None.

    Returns:
        str: HTML-escaped string representation of the value.
    """
    if value is None:
        return ""
    return html.escape(str(value))


def build_charge_summary_header(year: int, month: int) -> str:
    """
    Build a properly escaped charge summary header string.

    Args:
        year: The year (already validated by FastAPI)
        month: The month (already validated by FastAPI)

    Returns:
        str: HTML-safe formatted string "Charge Summary for YYYY-MM"
    """
    return f"Charge Summary for {escape_html(year)}-{escape_html(f'{month:02d}')}"


app = FastAPI()


@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root(
    year: int = Query(datetime.datetime.now().year, ge=2025, le=2027),
    month: int = Query(datetime.datetime.now().month, ge=1, le=12),
):
    # Build/commit meta for footer
    git_commit = os.environ.get("GIT_COMMIT", "")
    git_tag = os.environ.get("GIT_TAG", "")
    build_date = os.environ.get("BUILD_DATE", "")
    short_commit = git_commit[:7] if git_commit else ""

    # Parse and localize build date if present
    def _fmt_build_date(s: str) -> str:
        if not s:
            return ""
        try:
            iso = s
            if iso.endswith("Z"):
                iso = iso[:-1] + "+00:00"
            dt = datetime.datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone(TZ_CPH).strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            return s

    build_date_local = _fmt_build_date(build_date)
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
            <title>{build_charge_summary_header(year, month)}</title>
            <link href="https://unpkg.com/tailwindcss@^2/dist/tailwind.min.css" rel="stylesheet">
        </head>
        <body class="bg-black">
            <section class="bg-black">
                <div class="container px-5 py-12 mx-auto lg:px-20">
                    <div class="flex flex-col flex-wrap pb-6 mb-12 text-white">
                        <h1 class="mb-12 text-3xl font-medium text-white">
                            {build_charge_summary_header(year, month)}
                        </h1>
                        <p class="text-white text-xl">No charge data found for this month.</p>
                    </div>
                    <div class="text-center mt-8">
                        <a href="/?year={escape_html(prev_year)}&month={escape_html(prev_month)}" class="text-blue-400 hover:underline">« Previous Month</a>
                        <span class="mx-2 text-white">|</span>
                        <a href="/" class="text-blue-400 hover:underline">Home</a>
                        <span class="mx-2 text-white">|</span>
                        <a href="/?year={escape_html(next_year)}&month={escape_html(next_month)}" class="text-blue-400 hover:underline">Next Month »</a>
                    </div>
                    <div class="text-center mt-8 text-gray-400 text-sm">
                        Build:
                        {escape_html(git_tag) or 'untagged'}
                        {f'<a class="underline" href="https://github.com/tn8or/skoda/commit/{escape_html(git_commit)}" target="_blank" rel="noopener noreferrer">{escape_html(short_commit)}</a>' if git_commit else ''}
                        {f'({escape_html(build_date_local)})' if build_date_local else ''}
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
        <title>{build_charge_summary_header(year, month)}</title>
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
                        {build_charge_summary_header(year, month)}
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
                                <div class=\"divTableCell text-white\">{escape_html(d.strftime('%Y-%m-%d'))}</div>
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
    displayed_count = 0

    for sess in sessions:
        sess_rows = sess["rows"]
        mileage = sess["mileage"]
        stopped_at = sess["end"]
        # Session summary with away policy
        summary = compute_session_summary(sess_rows)
        amount = summary["amount"]
        price = summary["price"]
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
                # Assume naive as UTC, then convert to local time
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                dt_local = dt.astimezone(TZ_CPH)
                day = await ordinal(dt_local.day)
                stopped_at_str = (
                    dt_local.strftime("%a ") + day + " @ " + dt_local.strftime("%H:%M")
                )
            except Exception:
                stopped_at_str = str(stopped_at)
        else:
            stopped_at_str = ""
        # Filter out small sessions from display (< 1.0 kWh), but keep them in totals
        if amount < 1.0:
            continue
        displayed_count += 1

        html += f"""
                        <div class=\"divTableRow\">
                            <div class=\"divTableCell text-white\">{escape_html(stopped_at_str)}</div>
                            <div class=\"divTableCell text-white\">{escape_html(mileage)}</div>
                            <div class=\"divTableCell text-white\">{amount:.2f} kWh</div>
                            <div class=\"divTableCell text-white\">{price:.2f} DKK</div>
                            <div class=\"divTableCell text-white\">{int(charged_range / soc * 100) if charged_range and soc else 0} KM</div>
                            <div class=\"divTableCell text-white\">{range_diff if range_diff > 0 else 0} KM</div>
                            <div class=\"divTableCell text-white\">{range_per_kwh}</div>
                            <div class=\"divTableCell text-white\">{int(soc) if soc is not None else 0}%</div>
                            <div class=\"divTableCell text-white\">{escape_html(position)}</div>
                        </div>
        """
    # Compute month-wide mileage change across sessions for footer
    miles = [s["mileage"] for s in sessions if s.get("mileage") is not None]
    if miles:
        totalmileage = max(miles) - min(miles)
    avg_range_per_kwh = round(totalmileage / total_amount, 2) if total_amount > 0 else 0
    html += f"""
                    </div>
            <div class=\"divTableFoot\">
                        <div class=\"divTableRow font-bold\">
                <div class=\"divTableCell\">{displayed_count} charges</div>
                            <div class=\"divTableCell\">{totalmileage} KM</div>
                            <div class=\"divTableCell\">{total_amount:.2f} kWh</div>
                            <div class=\"divTableCell\">{total_price:.2f} DKK</div>
                            <div class=\"divTableCell\"></div>
                            <div class=\"divTableCell\"></div>
                            <div class=\"divTableCell\">Estimated: {round(total_range_per_kwh / range_count, 2) if total_range_per_kwh > 0 and range_count > 0 else 0}
                            <br />Actual: {avg_range_per_kwh if total_range_per_kwh > 0 and range_count > 0 else 0}</div>
                            <div class=\"divTableCell\"></div>
                            <div class=\"divTableCell\"></div>
                        </div>
                    </div>
                </div>
                <div class=\"text-center mt-8\">
                    <a href=\"/?year={escape_html(prev_year)}&month={escape_html(prev_month)}\" class=\"text-blue-400 hover:underline\">« Previous Month</a>
                    <span class=\"mx-2 text-white\">|</span>
                    <a href=\"/\" class=\"text-blue-400 hover:underline\">Home</a>
                    <span class=\"mx-2 text-white\">|</span>
                    <a href=\"/?year={escape_html(next_year)}&month={escape_html(next_month)}\" class=\"text-blue-400 hover:underline\">Next Month »</a>
                </div>
                <div class=\"text-center mt-4 text-gray-400 text-sm\">
                    Build:
                    {escape_html(git_tag) or 'untagged'}
                    {f'<a class=\"underline\" href=\"https://github.com/tn8or/skoda/commit/{escape_html(git_commit)}\" target=\"_blank\" rel=\"noopener noreferrer\">{escape_html(short_commit)}</a>' if git_commit else ''}
                    {f'({escape_html(build_date_local)})' if build_date_local else ''}
                    - <a class=\"underline\" href=\"https://github.com/tn8or/skoda/\" target=\"_blank\" rel=\"noopener noreferrer\">github.com/tn8or/skoda</a>
                </div>
            </div>
        </section>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.api_route("/health/rawlogs/age", methods=["GET", "HEAD"])
async def latest_rawlog_age(threshold_seconds: int | None = Query(default=None, ge=0)):
    """
    Report the age of the latest vehicle telemetry event in skoda.rawlogs.

    This endpoint now distinguishes between:
    1. General rawlogs (any log entry from the service)
    2. Vehicle telemetry logs (logs indicating actual car communication)

    The alerting logic is based on vehicle telemetry age, ensuring that even if
    the service is running and logging, we'll alert if the car isn't sending data.

    Query params:
      - threshold_seconds: optional non-negative integer. If provided and the
        latest vehicle event age exceeds this threshold, respond with HTTP 503.

    Responses:
      200 JSON: includes both general and vehicle log ages
      404 JSON: when no rawlogs are present yet
      500 JSON: on database error or timestamp parse error
      503 JSON: when vehicle logs are missing or threshold is exceeded
    """
    conn, cur = await db_connect(my_logger)

    # First, check if we have any rawlogs at all
    try:
        cur.execute("SELECT MAX(log_timestamp) FROM skoda.rawlogs")
        row = cur.fetchone()
    except Exception as e:
        my_logger.error("Error fetching latest rawlog timestamp: %s", e)
        return JSONResponse(
            status_code=500, content={"error": "database error fetching rawlogs"}
        )

    latest_general = row[0] if row else None
    if latest_general is None:
        return JSONResponse(
            status_code=404,
            content={
                "latest_timestamp": None,
                "age_seconds": None,
                "threshold_seconds": threshold_seconds,
                "within_threshold": None,
                "latest_general_timestamp": None,
                "general_age_seconds": None,
                "vehicle_log_patterns": list(VEHICLE_LOG_LIKE_PATTERNS),
                "message": "no rawlogs found",
            },
        )

    # Parse general timestamp
    try:
        if isinstance(latest_general, datetime.datetime):
            general_ts = latest_general
        else:
            general_ts = datetime.datetime.fromisoformat(str(latest_general))
        if general_ts.tzinfo is None:
            general_ts = general_ts.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return JSONResponse(
            status_code=500,
            content={
                "error": "could not parse latest rawlog timestamp",
                "raw": str(latest_general),
            },
        )

    # Now check for vehicle telemetry logs specifically
    # Use an efficient tiered search: check recent logs first (1 day),
    # then expand backward if needed. This avoids expensive full-table scans.
    vehicle_latest = None
    search_days = [1, 7, 30]  # Search windows in days

    for days_back in search_days:
        cutoff_time = (general_ts - datetime.timedelta(days=days_back)).isoformat()
        placeholders = " OR ".join(
            ["log_message LIKE ?"] * len(VEHICLE_LOG_LIKE_PATTERNS)
        )
        vehicle_query = f"SELECT MAX(log_timestamp) FROM skoda.rawlogs WHERE log_timestamp > ? AND ({placeholders})"
        params = (cutoff_time,) + VEHICLE_LOG_LIKE_PATTERNS

        try:
            cur.execute(vehicle_query, params)
            row = cur.fetchone()
            vehicle_latest = row[0] if row else None
            if vehicle_latest is not None:
                my_logger.debug(f"Found vehicle logs within {days_back} day(s)")
                break
        except Exception as e:
            my_logger.error("Error fetching vehicle rawlog timestamp: %s", e)
            return JSONResponse(
                status_code=500,
                content={"error": "database error fetching vehicle rawlogs"},
            )

    vehicle_latest = row[0] if row else None
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    general_age = max(0, int((now_utc - general_ts).total_seconds()))

    # CRITICAL: If we have general logs but no vehicle logs, alert!
    if vehicle_latest is None:
        return JSONResponse(
            status_code=503,
            content={
                "latest_timestamp": None,
                "age_seconds": None,
                "threshold_seconds": threshold_seconds,
                "within_threshold": False if threshold_seconds is not None else None,
                "latest_general_timestamp": general_ts.isoformat(),
                "general_age_seconds": general_age,
                "vehicle_log_patterns": list(VEHICLE_LOG_LIKE_PATTERNS),
                "message": "no vehicle telemetry logs found",
            },
        )

    # Parse vehicle timestamp
    try:
        if isinstance(vehicle_latest, datetime.datetime):
            vehicle_ts = vehicle_latest
        else:
            vehicle_ts = datetime.datetime.fromisoformat(str(vehicle_latest))
        if vehicle_ts.tzinfo is None:
            vehicle_ts = vehicle_ts.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return JSONResponse(
            status_code=500,
            content={
                "error": "could not parse latest vehicle rawlog timestamp",
                "raw": str(vehicle_latest),
            },
        )

    vehicle_age = max(0, int((now_utc - vehicle_ts).total_seconds()))
    within_threshold = (
        None if threshold_seconds is None else vehicle_age <= threshold_seconds
    )

    response_body = {
        "latest_timestamp": vehicle_ts.isoformat(),
        "age_seconds": vehicle_age,
        "threshold_seconds": threshold_seconds,
        "within_threshold": within_threshold,
        "latest_general_timestamp": general_ts.isoformat(),
        "general_age_seconds": general_age,
        "vehicle_log_patterns": list(VEHICLE_LOG_LIKE_PATTERNS),
    }

    # Threshold handling - alert if vehicle logs are too old
    if threshold_seconds is not None and vehicle_age > threshold_seconds:
        response_body["message"] = "out of bounds"
        return JSONResponse(status_code=503, content=response_body)

    return response_body
