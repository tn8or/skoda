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
