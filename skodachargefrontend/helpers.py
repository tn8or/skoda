import datetime
from typing import Any, Dict, List


def group_sessions_by_mileage(hourly: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Group hourly charge rows into sessions using mileage as the session key.

    Returns sessions sorted by end time.
    Each session: {"mileage", "rows", "start", "end"}.
    """
    sessions_map: Dict[Any, Dict[str, Any]] = {}
    for rec in hourly:
        key = rec.get("mileage")
        if key not in sessions_map:
            sessions_map[key] = {
                "mileage": key,
                "rows": [],
                "start": None,
                "end": None,
            }
        sess = sessions_map[key]
        sess["rows"].append(rec)
        st = rec.get("start_at") or rec.get("log_timestamp")
        sp = rec.get("stop_at") or rec.get("log_timestamp")
        if st and (sess["start"] is None or st < sess["start"]):
            sess["start"] = st
        if sp and (sess["end"] is None or sp > sess["end"]):
            sess["end"] = sp
    sessions = sorted(
        sessions_map.values(),
        key=lambda s: (s.get("end") or s.get("start") or datetime.datetime.min),
    )
    return sessions


def compute_session_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compute aggregates for a session and apply away-price policy.

    If any row has position != "home", price is set to 0.0 and position is
    "away"; otherwise price is the sum of hourly prices and position is "home".
    """
    amount = round(sum(float(r.get("amount", 0.0)) for r in rows), 2)
    any_away = any(r.get("position") != "home" for r in rows)
    price = 0.0 if any_away else round(sum(float(r.get("price", 0.0)) for r in rows), 2)
    position = "away" if any_away else "home"
    return {
        "amount": amount,
        "price": price,
        "position": position,
        "any_away": any_away,
    }


def compute_daily_totals_home(
    hourly: List[Dict[str, Any]],
) -> Dict[datetime.date, Dict[str, float]]:
    """
    Compute daily totals (kWh and DKK) including only rows where position is
    "home". Rows without a stop_at datetime are ignored.
    """
    from collections import defaultdict

    daily: Dict[datetime.date, Dict[str, float]] = defaultdict(
        lambda: {"kwh": 0.0, "dkk": 0.0}
    )
    for rec in hourly:
        dt = rec.get("stop_at")
        if not dt:
            continue
        if rec.get("position") == "home":
            d = dt.date()
            daily[d]["kwh"] += float(rec.get("amount", 0.0))
            daily[d]["dkk"] += float(rec.get("price", 0.0))
    return daily


def compute_normalized_efficiency(
    sessions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Compute range efficiency (both estimated and actual) for each charge session.

    For each session, calculates:
      estimated_efficiency = (range_gain / soc_gain) * 100
      actual_efficiency = (mileage_driven_since_last_charge / soc_gain) * 100

    This represents the km of range/actual-drive per 1% increase in battery,
    normalized to what a full (100%) charge would provide.

    Actual efficiency is calculated by comparing mileage at the start of this
    charge to the end of the previous charge, reflecting real-world driving
    efficiency between charging sessions.

    Returns a list of dicts with:
      - stop_at: session end timestamp
      - estimated_efficiency: km of range per 100% charge (from car's estimate)
      - actual_efficiency: km driven since last charge per 100% charge (from mileage)
      - range_gain: range gained during charge
      - mileage_driven: actual km driven since last charge
      - soc_gain: SOC % gained during charge
      - charged_range: range at end of charge
      - start_range: range at start of charge
      - start_soc: SOC at start
      - end_soc: SOC at end
    """
    results: List[Dict[str, Any]] = []
    prev_session_mileage = None

    for sess in sessions:
        rows = sess.get("rows", [])
        if not rows:
            continue

        stop_at = sess.get("end")
        rows_with_range = [
            r
            for r in rows
            if r.get("charged_range") is not None and r.get("start_range") is not None
        ]

        if not rows_with_range:
            continue

        # Get range values from first and last rows with range data
        first_row = rows_with_range[0]
        last_row = rows_with_range[-1]

        start_range = float(first_row.get("start_range", 0))
        charged_range = float(last_row.get("charged_range", 0))
        range_gain = charged_range - start_range

        # Get SOC values
        soc_vals = [float(r.get("soc")) for r in rows if r.get("soc") is not None]
        if len(soc_vals) < 2:
            # Need both start and end SOC for meaningful efficiency
            continue

        start_soc = soc_vals[0]
        end_soc = soc_vals[-1]
        soc_gain = end_soc - start_soc

        # Get mileage at start and end of this charge session
        mileage_vals = [r.get("mileage") for r in rows if r.get("mileage") is not None]
        current_session_start_mileage = min(mileage_vals) if mileage_vals else None
        current_session_end_mileage = max(mileage_vals) if mileage_vals else None

        # Calculate mileage driven SINCE last charge (between sessions)
        mileage_driven = 0
        if (
            prev_session_mileage is not None
            and current_session_start_mileage is not None
        ):
            mileage_driven = current_session_start_mileage - prev_session_mileage
            # Handle case where mileage wrapped around (shouldn't happen but be safe)
            if mileage_driven < 0:
                mileage_driven = 0

        # Update for next iteration
        if current_session_end_mileage is not None:
            prev_session_mileage = current_session_end_mileage

        # Calculate estimated efficiency (from car's range estimate)
        estimated_efficiency = None
        if soc_gain > 0 and range_gain > 0:
            # Normalize to 100% charge: km per 1% * 100
            estimated_efficiency = round((range_gain / soc_gain) * 100, 2)

        # Calculate actual efficiency (from actual mileage driven since last charge)
        # Only include if we have real mileage data from previous session
        actual_efficiency = None
        if soc_gain > 0 and mileage_driven > 0:
            # Normalize to 100% charge: km per 1% * 100
            actual_efficiency = round((mileage_driven / soc_gain) * 100, 2)

        results.append(
            {
                "stop_at": stop_at,
                "estimated_efficiency": estimated_efficiency,
                "actual_efficiency": actual_efficiency,
                "range_gain": round(range_gain, 2),
                "mileage_driven": mileage_driven,
                "soc_gain": round(soc_gain, 2),
                "charged_range": round(charged_range, 2),
                "start_range": round(start_range, 2),
                "start_soc": round(start_soc, 2),
                "end_soc": round(end_soc, 2),
            }
        )

    return results


def compute_footer_metrics(sessions: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Compute footer metrics:
    - totalmileage: max(mileage) - min(mileage), ignoring None
    - total_amount: sum of session amounts
    - estimated_km_per_kwh: average of per-session (range_diff/amount) where
      both values are > 0
    - actual_km_per_kwh: totalmileage / total_amount when possible
    """
    # total mileage across sessions
    miles = [s.get("mileage") for s in sessions if s.get("mileage") is not None]
    totalmileage = (max(miles) - min(miles)) if miles else 0

    # compute sums
    total_amount = 0.0
    per_session_eff: List[float] = []
    for sess in sessions:
        rows = sess.get("rows", [])
        amount = sum(float(r.get("amount", 0.0)) for r in rows)
        # range diff
        charged_range_vals = [
            r.get("charged_range") for r in rows if r.get("charged_range") is not None
        ]
        start_range_vals = [
            r.get("start_range") for r in rows if r.get("start_range") is not None
        ]
        range_diff = 0.0
        if charged_range_vals and start_range_vals:
            range_diff = float(max(charged_range_vals) - min(start_range_vals))
        if amount > 0 and range_diff > 0:
            per_session_eff.append(range_diff / amount)
        total_amount += amount

    estimated = (
        round(sum(per_session_eff) / len(per_session_eff), 2)
        if per_session_eff
        else 0.0
    )
    actual = round(totalmileage / total_amount, 2) if total_amount > 0 else 0.0
    return {
        "totalmileage": totalmileage,
        "total_amount": round(total_amount, 2),
        "estimated_km_per_kwh": estimated,
        "actual_km_per_kwh": actual,
    }
