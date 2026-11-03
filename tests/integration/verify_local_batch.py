"""Opt-in live KIS batch measurement. Uses a fresh, isolated local SQLite only.

No credentials, authorization headers, tokens, or provider payloads are logged.
Run from the repository root: python -m tests.integration.verify_local_batch ...
"""

import argparse
import json
import time
from collections import Counter
from dataclasses import replace
from datetime import date
from pathlib import Path

from app.clients.kis import KisClient
from app.config import Settings
from app.domain import RiskError, now_kst
from app.repositories.sqlite import Repository
from app.services.batch import run_batch
from app.services.calendar import TradingCalendar
from app.universe import load_universe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calendar-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    db_path = args.output_dir / "risk.sqlite3"
    if db_path.exists():
        raise SystemExit("Refusing to overwrite an existing validation database")
    settings = replace(Settings.from_env(), database=db_path,
                       calendar=args.calendar_dir / "open-days.csv",
                       trading_hours=args.calendar_dir / "trading-hours.csv")
    calendar = TradingCalendar.from_csv(settings.calendar, settings.trading_hours)
    started = now_kst()
    calendar.batch_target(started, args.as_of)
    sessions = calendar.window(args.as_of)
    universe = load_universe(settings.universe)
    calls, statuses, per_stock = Counter(), Counter(), []
    progress_path = args.output_dir / "progress.jsonl"

    class MeasuredClient(KisClient):
        def history(self, ticker, start, end):
            began, previous = time.perf_counter(), sum(calls.values())
            item = {"ticker": ticker}
            try:
                prices = super().history(ticker, start, end)
                item.update(rows=len(prices), first=str(prices[0].day) if prices else None,
                            last=str(prices[-1].day) if prices else None)
                return prices
            except RiskError as exc:
                item["collection_error"] = exc.code
                raise
            finally:
                item.update(seconds=round(time.perf_counter() - began, 3), requests=sum(calls.values()) - previous)
                per_stock.append(item)
                with progress_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(item) + "\n")
                print(json.dumps(item), flush=True)

    client = MeasuredClient(settings.kis_key, settings.kis_secret, settings.kis_environment)
    client.http.event_hooks["request"] = [lambda request: calls.update([request.method + " " + request.url.path])]
    client.http.event_hooks["response"] = [lambda response: statuses.update([str(response.status_code)])]
    before = time.perf_counter()
    report = {"started_at": started.isoformat(), "target": str(args.as_of),
              "kis_environment": settings.kis_environment, "calendar_version": calendar.version,
              "universe_count": len(universe), "active_count": sum(i.status == "active" for i in universe.values()),
              "expected_price_sessions": len(sessions), "window_start": str(sessions[0])}
    try:
        report["result"] = run_batch(Repository(db_path), calendar, universe, client, started, target_date=args.as_of)
    except RiskError as exc:
        report["error"] = exc.code
    finally:
        client.close()
        report.update(finished_at=now_kst().isoformat(), elapsed_seconds=round(time.perf_counter() - before, 3),
                      requests=dict(calls), http_statuses=dict(statuses), per_stock=per_stock)
        (args.output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "per_stock"}), flush=True)
    return 1 if report.get("error") or report.get("result", {}).get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
