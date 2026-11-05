"""Real FastAPI HTTP test fixture; synthetic data only, no KIS or local .env.

Invoked by MARIA's opt-in PythonRiskHttpIntegrationTest. Production endpoints
are unchanged; faults are injected by moving this fixture's SQLite file.
"""

import argparse
import csv
import json
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def setup(directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "empty.env").write_text("", encoding="utf-8")
    os.environ["RISK_ENV_FILE"] = str(directory / "empty.env")
    # Do not allow inherited production/shared settings into module-level app creation.
    for key in ("KIS_ENV_FILE", "KIS_APP_KEY", "KIS_APP_SECRET", "RISK_CALENDAR_PATH",
                "RISK_TRADING_HOURS_PATH", "RISK_DB_PATH", "RISK_UNIVERSE_PATH",
                "RISK_SERVICE_KEY", "RISK_SCHEDULER_ENABLED", "RISK_SCHEDULER_POLL_SECONDS",
                "RISK_BATCH_MAX_ATTEMPTS", "RISK_BACKUP_DIR", "RISK_BACKUP_KEEP"):
        os.environ.pop(key, None)


def prepare(directory):
    from app.domain import KST
    from app.services.calendar import TradingCalendar

    path = directory / "calendar.csv"
    if path.exists():
        return
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["date", "is_open", "regular_close_at", "source_ref"])
        day = date(2023, 10, 1)
        while day <= date(2026, 11, 5):
            opened = day.weekday() < 5 and day != date(2026, 10, 27)
            close = datetime(day.year, day.month, day.day,
                             16 if day == date(2026, 10, 29) else 15, 30, tzinfo=KST)
            writer.writerow([day.isoformat(), int(opened), close.isoformat() if opened else "",
                             "synthetic-not-an-official-KRX-schedule" if opened else ""])
            day += timedelta(days=1)
    (directory / "universe.csv").write_text(
        "ticker,name,status,kind\n005930,Synthetic A,active,STOCK\n000660,Synthetic B,active,STOCK\n", encoding="utf-8")
    (directory / "clock.txt").write_text("2026-10-28T17:10:00+09:00", encoding="utf-8")
    calendar = TradingCalendar.from_csv(path)
    (directory / "manifest.json").write_text(json.dumps({
        "synthetic": True, "calendarVersion": calendar.version,
        "coverageStart": "2023-10-01", "coverageEnd": "2026-11-05",
    }), encoding="utf-8")


def publish(directory, ticker, revision):
    from app.domain import Price
    from app.repositories.sqlite import Repository
    from app.services.calendar import TradingCalendar
    from app.services.calculation import calculate_grade

    calendar = TradingCalendar.from_csv(directory / "calendar.csv")
    sessions = calendar.window(date(2026, 10, 28))
    amount, prices = Decimal("100000"), []
    for index, day in enumerate(sessions):
        if index:
            amount *= 1 + Decimal((index * 7) % 17 - 8) / 1000
        close = amount * Decimal("1.03") if revision == 2 and index == 100 else amount
        prices.append(Price(day, close, 100))
    repo = Repository(directory / "risk.sqlite3")
    repo.initialize()
    grade = calculate_grade(ticker, prices, sessions,
                            datetime.fromisoformat(f"2026-10-28T15:41:0{revision}.123456+09:00"))
    published, changed = repo.replace(grade, prices)
    return {"analysisVersion": published.analysis_version, "changed": changed}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("serve", "publish"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--ticker", choices=("005930", "000660"), default="005930")
    parser.add_argument("--revision", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    directory = args.directory.resolve()
    setup(directory)
    prepare(directory)
    if args.mode == "publish":
        print(json.dumps(publish(directory, args.ticker, args.revision)))
        return
    if not 1024 <= args.port <= 65535:
        raise ValueError("Explicit unprivileged loopback port required")
    publish(directory, "005930", 1)
    from app.config import Settings
    from app.main import create_app
    import uvicorn

    settings = Settings(database=directory / "risk.sqlite3", calendar=directory / "calendar.csv",
                        universe=directory / "universe.csv", service_key='synthetic-http-integration-key')
    class SyntheticStatusClient:
        def __init__(self, *_):
            pass
        def stock_elevated(self, ticker):
            from app.domain import RiskError
            if (directory / 'status-unavailable').exists():
                raise RiskError('KIS_UNAVAILABLE', 'Synthetic provider failure', 503)
            path = directory / 'stock-status.json'
            return json.loads(path.read_text(encoding='utf-8')).get(ticker, False) if path.exists() else False
        def close(self):
            pass

    api = create_app(settings, clock=lambda: datetime.fromisoformat(
        (directory / "clock.txt").read_text(encoding="utf-8")),
        classification_client_factory=SyntheticStatusClient)
    uvicorn.run(api, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
