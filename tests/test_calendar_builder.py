"""Calendar generation boundary checks; no XLS reader or network required."""

from datetime import date, datetime
import hashlib
import json
from pathlib import Path

import pytest

from app.domain import KST, RiskError
from app.services.calendar import TradingCalendar
from tools.build_krx_calendar import SPECIAL_CLOSES, calendar_rows, write_csv


def build(tmp_path, start, end, holidays, reviewed):
    days, hours = calendar_rows(start, end, holidays, reviewed)
    dates_path, hours_path = tmp_path / "days.csv", tmp_path / "hours.csv"
    write_csv(dates_path, ["date", "is_open", "reason", "source_ref"], days)
    write_csv(hours_path, ["date", "regular_close_at", "available_at", "source_ref"], hours)
    return TradingCalendar.from_csv(dates_path, hours_path)


def test_public_holiday_and_weekend_are_not_sessions(tmp_path):
    cal = build(tmp_path, date(2026, 10, 1), date(2026, 10, 6),
                {date(2026, 10, 5): "substitute holiday"}, date(2026, 10, 6))
    assert cal.sessions == [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 6)]
    assert cal.batch_target(datetime(2026, 10, 5, 17, tzinfo=KST)) is None


@pytest.mark.parametrize("day", [date(2023, 11, 16), date(2024, 11, 14), date(2025, 11, 13)])
def test_known_krx_changed_close_starts_batch_at_1640(tmp_path, day):
    cal = build(tmp_path, day, day, {}, day)
    at = lambda h, m: datetime(day.year, day.month, day.day, h, m, tzinfo=KST)
    assert cal.batch_target(at(15, 40)) is None
    assert cal.batch_target(at(16, 39)) is None
    assert cal.batch_target(at(16, 40)) == day
    assert cal.available == {}


def test_normal_day_market_reference_and_batch_start_are_distinct(tmp_path):
    cal = build(tmp_path, date(2026, 10, 28), date(2026, 10, 29), {}, date(2026, 10, 29))
    at = lambda h, m: datetime(2026, 10, 29, h, m, tzinfo=KST)
    assert cal.latest(at(15, 29)) == date(2026, 10, 28)
    assert cal.latest(at(15, 30)) == date(2026, 10, 29)
    assert cal.batch_target(at(15, 39)) is None
    assert cal.batch_target(at(15, 40)) == date(2026, 10, 29)


def test_future_open_day_does_not_inherit_unreviewed_regular_hours(tmp_path):
    cal = build(tmp_path, date(2026, 10, 29), date(2026, 12, 31),
                {date(2026, 12, 31): "year-end holiday"}, date(2026, 10, 29))
    for day in (date(2026, 10, 30), date(2026, 11, 19)):
        now = datetime(day.year, day.month, day.day, 17, tzinfo=KST)
        for action in (lambda: cal.latest(now), lambda: cal.batch_target(now)):
            with pytest.raises(RiskError) as error:
                action()
            assert error.value.code == "TRADING_HOURS_UNAVAILABLE"
    with pytest.raises(RiskError) as error:
        cal.latest(datetime(2027, 1, 1, 17, tzinfo=KST))
    assert error.value.code == "CALENDAR_OUT_OF_RANGE"


@pytest.mark.parametrize("start,end,reviewed", [
    (date(2026, 1, 2), date(2026, 1, 1), date(2026, 1, 1)),
    (date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)),
    (date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 1)),
])
def test_invalid_review_range_is_rejected(start, end, reviewed):
    with pytest.raises(ValueError):
        calendar_rows(start, end, {}, reviewed)


def test_shared_snapshot_hashes_history_and_real_holiday_boundaries():
    folder = Path(__file__).resolve().parents[1] / "config/krx"
    report = json.loads((folder / "verification.json").read_text(encoding="utf-8"))
    assert report["serialization"] == {"encoding": "utf-8", "line_ending": "LF"}
    for name, expected in report["sha256"].items():
        raw = (folder / name).read_bytes()
        assert b"\r" not in raw
        assert hashlib.sha256(raw).hexdigest() == expected
        # Git/Linux LF normalization must leave the published bytes unchanged.
        assert hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() == expected
    cal = TradingCalendar.from_csv(folder / "open-days.csv", folder / "trading-hours.csv")
    assert cal.days[0][0] == date(2023, 1, 1)
    assert cal.days[-1][0] == date(2026, 12, 31)
    assert len(cal.days) == report["calendar_days"] == 1461
    assert len(cal.sessions) == report["open_days"] == 975
    assert len(cal.window(date(2026, 10, 29))) == 731
    assert cal.window(date(2026, 10, 29))[0] == date(2023, 10, 27)
    assert date(2026, 10, 5) not in cal.sessions
    assert cal.latest(datetime(2026, 10, 24, 12, tzinfo=KST)) == date(2026, 10, 23)
    assert cal.state(date(2026, 10, 26), date(2026, 10, 29)) == "STALE"
    assert cal.state(date(2026, 10, 23), date(2026, 10, 29)) == "EXPIRED"
    assert report["hours_through"] == "2026-12-31"
    assert report["hours_policy"] == "official_regular_hours_with_unconfirmed_exceptions"
    assert [row["date"] for row in report["unconfirmed_special_sessions"]] == ["2026-11-19"]
    assert report["hours_rows"] == 974 and report["unknown_hours_open_days"] == 1
    assert cal.batch_target(datetime(2026, 10, 30, 15, 40, tzinfo=KST)) == date(2026, 10, 30)
    with pytest.raises(RiskError) as exc:
        cal.batch_target(datetime(2026, 11, 19, 17, tzinfo=KST))
    assert exc.value.code == "TRADING_HOURS_UNAVAILABLE"
    assert cal.batch_target(datetime(2026, 11, 2, 15, 40, tzinfo=KST)) == date(2026, 11, 2)
    assert cal.batch_target(datetime(2026, 11, 20, 15, 40, tzinfo=KST)) == date(2026, 11, 20)


def test_unconfirmed_exam_day_does_not_block_surrounding_regular_sessions(tmp_path):
    cal = build(tmp_path, date(2026, 11, 18), date(2026, 11, 20), {}, date(2026, 11, 20))
    assert date(2026, 11, 19) in cal.sessions  # Open date, not a holiday.
    assert cal.closes[date(2026, 11, 19)] is None
    for day in (18, 20):
        assert cal.batch_target(datetime(2026, 11, day, 15, 39, tzinfo=KST)) is None
        assert cal.latest_batch_target(datetime(2026, 11, day, 15, 40, tzinfo=KST)) == date(2026, 11, day)
    now = datetime(2026, 11, 19, 17, tzinfo=KST)
    for action in (lambda: cal.latest(now), lambda: cal.batch_target(now), lambda: cal.latest_batch_target(now)):
        with pytest.raises(RiskError) as error:
            action()
        assert error.value.code == "TRADING_HOURS_UNAVAILABLE"
    # Expiry still counts the verified open date even when its close is unknown.
    assert cal.state(date(2026, 11, 18), date(2026, 11, 20)) == "STALE"


def test_confirmed_special_override_unblocks_only_after_its_delayed_close(tmp_path, monkeypatch):
    day = date(2026, 11, 19)
    # Synthetic approval path; does not assert 2026 KRX hours are confirmed.
    monkeypatch.setitem(SPECIAL_CLOSES, day, ("16:30", "synthetic-confirmed-notice"))
    cal = build(tmp_path, day, day, {}, day)
    assert cal.batch_target(datetime(2026, 11, 19, 15, 40, tzinfo=KST)) is None
    assert cal.batch_target(datetime(2026, 11, 19, 16, 39, tzinfo=KST)) is None
    assert cal.batch_target(datetime(2026, 11, 19, 16, 40, tzinfo=KST)) == day
