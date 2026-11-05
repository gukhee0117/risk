"""Synthetic dates/prices only: these fixtures are NOT an official KRX calendar."""

import csv
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from app.config import Settings
from app.domain import KST, Price
from app.repositories.sqlite import Repository
from app.services.calendar import TradingCalendar
from app.universe import Instrument


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    # Never load actual developer keys or files while running tests.
    path = tmp_path / "test.env"
    path.write_text("", encoding="utf-8")
    monkeypatch.setenv("RISK_ENV_FILE", str(path))
    for name in ("KIS_ENV_FILE", "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ENV",
                 "RISK_CALENDAR_PATH", "RISK_TRADING_HOURS_PATH", "RISK_DB_PATH", "RISK_UNIVERSE_PATH",
                 "RISK_SERVICE_KEY", "RISK_SCHEDULER_ENABLED", "RISK_SCHEDULER_POLL_SECONDS",
                 "RISK_BATCH_MAX_ATTEMPTS", "RISK_BACKUP_DIR", "RISK_BACKUP_KEEP"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def market(tmp_path):
    days = []
    current = date(2023, 10, 1)
    while current <= date(2026, 11, 15):
        available = datetime.combine(current, time(16), KST) if current.weekday() < 5 else None
        days.append((current, available))
        current += timedelta(days=1)
    calendar = TradingCalendar(days)
    now = datetime(2026, 10, 29, 12, tzinfo=KST)
    end = calendar.latest(now)
    sessions = calendar.window(end)
    path = tmp_path / "synthetic-calendar.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["date", "is_open", "regular_close_at", "source_ref"])
        writer.writerows((day.isoformat(), int(available is not None), available.isoformat() if available else "", "synthetic-test-only") for day, available in days)
    universe_path = tmp_path / "universe.csv"
    universe_path.write_text("ticker,name,status,kind,inception_date\n005930,synthetic A,active,FUND,2020-01-01\n000660,synthetic B,active,FUND,2020-01-01\n385720,wrong,code_mismatch,FUND,2020-01-01\n", encoding="utf-8")
    repository = Repository(tmp_path / "risk.sqlite3")
    repository.initialize()
    universe = {ticker: Instrument(ticker, ticker, "active", "FUND", date(2020, 1, 1)) for ticker in ("005930", "000660")}
    settings = Settings(repository.path, path, universe_path, service_key='synthetic-test-service-key')
    return calendar, now, sessions, repository, universe, settings


def make_prices(sessions, changes=None):
    changes = changes if changes is not None else [Decimal("-.02") if i % 2 == 0 else Decimal(".01") for i in range(len(sessions) - 1)]
    amount = Decimal(100000)
    prices = [Price(sessions[0], amount, 100)]
    for day, change in zip(sessions[1:], changes):
        amount *= 1 + Decimal(str(change))
        prices.append(Price(day, amount, 100))
    return prices
