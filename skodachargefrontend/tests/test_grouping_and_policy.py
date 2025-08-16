import datetime

from helpers import (
    compute_daily_totals_home,
    compute_footer_metrics,
    compute_session_summary,
    group_sessions_by_mileage,
)


def make_row(
    day: int, hour: int, pos: str = "home", amount=1.0, price=2.0, mileage=1000
):
    ts = datetime.datetime(2025, 8, day, hour, 0, 0)
    return {
        "log_timestamp": ts,
        "start_at": ts,
        "stop_at": ts,
        "amount": float(amount),
        "price": float(price),
        "charged_range": None,
        "start_range": None,
        "mileage": mileage,
        "position": pos,
        "soc": None,
    }


def test_group_sessions_by_mileage_collapses_rows():
    rows = [
        make_row(12, 0, mileage=1111),
        make_row(12, 1, mileage=1111),
        make_row(12, 2, mileage=2222),
        make_row(12, 3, mileage=2222),
    ]
    sessions = group_sessions_by_mileage(rows)
    assert len(sessions) == 2
    s1, s2 = sessions
    assert s1["mileage"] in {1111, 2222}
    assert s2["mileage"] in {1111, 2222}
    # Check start/end boundaries
    for s in sessions:
        assert s["start"] <= s["end"]
        assert len(s["rows"]) == 2


def test_compute_session_summary_home_prices_sum():
    rows = [
        make_row(12, 0, pos="home", amount=2.5, price=3.0),
        make_row(12, 1, pos="home", amount=1.5, price=2.0),
    ]
    summary = compute_session_summary(rows)
    assert summary["any_away"] is False
    assert summary["position"] == "home"
    assert summary["amount"] == 4.0
    assert summary["price"] == 5.0


def test_compute_session_summary_any_away_zero_price():
    rows = [
        make_row(12, 0, pos="home", amount=2.5, price=3.0),
        make_row(12, 1, pos="away", amount=1.5, price=2.0),
    ]
    summary = compute_session_summary(rows)
    assert summary["any_away"] is True
    assert summary["position"] == "away"
    assert summary["amount"] == 4.0
    assert summary["price"] == 0.0


def test_compute_daily_totals_home_excludes_away():
    rows = [
        make_row(12, 0, pos="home", amount=2.0, price=4.0),
        make_row(12, 1, pos="away", amount=5.0, price=10.0),
        make_row(13, 0, pos="home", amount=3.0, price=6.0),
    ]
    daily = compute_daily_totals_home(rows)
    d1 = datetime.date(2025, 8, 12)
    d2 = datetime.date(2025, 8, 13)
    assert daily[d1]["kwh"] == 2.0
    assert daily[d1]["dkk"] == 4.0
    assert daily[d2]["kwh"] == 3.0
    assert daily[d2]["dkk"] == 6.0


def test_compute_footer_metrics_basic():
    # two sessions at mileages 1000 and 1200
    s1_rows = [
        make_row(12, 0, mileage=1000, amount=2.0, price=1.0),
        make_row(12, 1, mileage=1000, amount=2.0, price=1.0),
    ]
    s2_rows = [
        make_row(13, 0, mileage=1200, amount=1.0, price=1.0),
    ]
    sessions = [
        {
            "mileage": 1000,
            "rows": s1_rows,
            "start": s1_rows[0]["start_at"],
            "end": s1_rows[-1]["stop_at"],
        },
        {
            "mileage": 1200,
            "rows": s2_rows,
            "start": s2_rows[0]["start_at"],
            "end": s2_rows[-1]["stop_at"],
        },
    ]
    footer = compute_footer_metrics(sessions)
    assert footer["totalmileage"] == 200
    assert footer["total_amount"] == 5.0
    # No range info, so estimated is 0; actual is totalmileage/total_amount
    assert footer["estimated_km_per_kwh"] == 0.0
    assert footer["actual_km_per_kwh"] == round(200 / 5.0, 2)
