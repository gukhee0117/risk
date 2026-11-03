from datetime import date, datetime, timedelta

import pytest

from app.domain import KST, RiskError
from app.services.calendar import TradingCalendar


def calendar():
    # Synthetic holiday on Monday; no weekday inference in production selection.
    start = date(2026, 10, 29)
    days = [start + timedelta(days=i) for i in range(8)]
    opened = {date(2026, 10, 29), date(2026, 10, 30), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)}
    return TradingCalendar([(d, datetime(d.year, d.month, d.day, 15, 30, tzinfo=KST) if d in opened else None) for d in days])


def test_default_previous_session_and_close_gate():
    cal = calendar()
    assert cal.price_target(datetime(2026, 11, 3, 15, 39, tzinfo=KST)) is None
    assert cal.price_target(datetime(2026, 11, 3, 15, 40, tzinfo=KST)) == date(2026, 10, 30)
    assert cal.price_target(datetime(2026, 11, 4, 15, 40, tzinfo=KST)) == date(2026, 11, 3)


def test_holiday_recovery_does_not_collect_latest_friday():
    cal = calendar()
    for day in (date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2)):
        now = datetime.combine(day, datetime.min.time(), KST).replace(hour=17)
        assert cal.price_target(now) is None
        assert cal.latest_price_target(now) == date(2026, 10, 29)
        with pytest.raises(RiskError, match="다음 거래일"):
            cal.price_target(now, date(2026, 10, 30))


def test_explicit_recovery_and_current_day_rejection():
    cal = calendar()
    now = datetime(2026, 11, 3, 17, tzinfo=KST)
    assert cal.price_target(now, date(2026, 10, 30)) == date(2026, 10, 30)
    with pytest.raises(RiskError) as exc:
        cal.price_target(now, date(2026, 11, 3))
    assert exc.value.code == "BATCH_NOT_DUE"


def test_morning_restart_preserves_previous_execution_target():
    cal = calendar()
    assert cal.latest_price_target(datetime(2026, 11, 4, 9, tzinfo=KST)) == date(2026, 10, 30)


def test_window_and_expiry_remain_based_on_price_date(market):
    cal, _, _, *_ = market
    now = datetime(2026, 11, 2, 17, tzinfo=KST)
    target = cal.price_target(now)
    assert target == date(2026, 10, 30)
    assert cal.window(target)[-1] == target
    assert cal.state(target, cal.latest(now)) == "STALE"
    assert cal.state(target, date(2026, 11, 5)) == "EXPIRED"


def test_default_batch_requests_and_stores_previous_date_window(market):
    from app.services.batch import run_batch
    from conftest import make_prices
    cal, _, _, repo, universe, _ = market
    now = datetime(2026, 11, 2, 17, tzinfo=KST)
    target = date(2026, 10, 30)
    sessions = cal.window(target)
    calls = []

    class Client:
        def history(self, ticker, start, end):
            calls.append((start, end))
            return make_prices(sessions)

    result = run_batch(repo, cal, universe, Client(), now, clock=lambda: now)
    assert result["priceTo"] == str(target)
    assert result["updated"] == list(universe)
    assert calls == [(sessions[0], target)] * len(universe)
    with repo.snapshot() as db:
        assert all(grade.price_to == target for grade in repo.grades(db).values())


def test_manual_current_day_fails_before_client_construction(market, monkeypatch, capsys):
    import json
    from app import cli
    _, _, _, _, _, settings = market
    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(cli, "now_kst", lambda: datetime(2026, 11, 2, 17, tzinfo=KST))
    monkeypatch.setattr("sys.argv", ["risk", "batch", "--as-of", "2026-11-02"])
    monkeypatch.setattr(cli, "KisClient", lambda *args: pytest.fail("must not call KIS"))
    assert cli.main() == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "BATCH_NOT_DUE"
