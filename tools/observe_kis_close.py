"""Read-only KIS daily-bar observations. Never publishes grades or proves finality."""

import argparse
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from app.clients.kis import KisClient
from app.config import ROOT, Settings
from app.domain import RiskError, now_kst
from app.services.batch import batch_lock
from app.services.calendar import TradingCalendar
from app.universe import load_universe

DEFAULT_TICKERS = ("005930", "247540", "069500")


def plan(calendar, target):
    if target not in calendar.sessions:
        raise RiskError("INVALID_BATCH_DATE", "공식 개장일을 지정하세요.", 400)
    close = calendar.closes[target]
    if close is None:
        raise RiskError("TRADING_HOURS_UNAVAILABLE", "공식 종료 시각을 먼저 연결하세요.", 503)
    return {"target": str(target), "calendar_version": calendar.version,
            "regular_close_at": close.isoformat(),
            "suggested_observations": [(close + timedelta(minutes=n)).isoformat() for n in (10, 15, 20, 30, 60)],
            "purpose": "observation_only", "provider_finality": "NOT_ESTABLISHED"}


def digest(ticker, target, value):
    return hashlib.sha256(f"{ticker}|{target}|{value}".encode()).hexdigest()


def observe(client, calendar, target, tickers, environment, *, clock=now_kst, previous=()):
    # Enforce the same close+10 rule before any API call. Log actual times only.
    calendar.batch_target(clock(), target)
    records = []
    for ticker in tickers:
        record = {"ticker": ticker, "target": str(target), "environment": environment,
                  "calendar_version": calendar.version, "started_at": clock().isoformat(),
                  "purpose": "observation_only", "provider_finality": "NOT_ESTABLISHED"}
        fatal = False
        try:
            prices = client.history(ticker, target, target)
            if len(prices) > 1 or any(p.day != target for p in prices):
                raise RiskError("INVALID_KIS_DATA", "조회 대상 날짜가 일치하지 않습니다.")
            if not prices:
                record.update(status="TARGET_MISSING", comparison="NO_VALID_ROW")
            else:
                price = prices[0]
                value = format(price.close, "f")
                value = value.rstrip("0").rstrip(".") if "." in value else value
                record.update(status="ROW_PRESENT" if price.volume else "ZERO_VOLUME",
                              close_hash=digest(ticker, target, value),
                              volume_hash=digest(ticker, target, price.volume))
                old = next((r for r in reversed(previous) if all(r.get(k) == record[k] for k in
                            ("ticker", "target", "environment", "calendar_version")) and "close_hash" in r), None)
                fields = [f for f in ("close", "volume") if old and old.get(f + "_hash") != record[f + "_hash"]]
                record.update(comparison="BASELINE" if old is None else "CHANGED" if fields else "UNCHANGED",
                              changed_fields=fields)
        except RiskError as exc:
            record.update(status="ERROR", error_code=exc.code, comparison="NO_VALID_ROW")
            fatal = exc.code in {"KIS_CONFIGURATION", "KIS_AUTH_FAILED", "KIS_RESPONSE_ERROR", "KIS_QUERY_FAILED"}
        record["observed_at"] = clock().isoformat()
        records.append(record)
        if fatal:
            break
    return records


def read_observations(path):
    if not path.exists():
        return []
    try:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if any(not isinstance(r, dict) for r in records):
            raise ValueError
        return records
    except (OSError, UnicodeError, ValueError) as exc:
        raise RiskError("INVALID_OBSERVATION_LOG", "기존 관측 파일을 확인하세요. 덮어쓰지 않습니다.", 503) from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "snapshot"))
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--calendar-dir", type=Path, default=ROOT / "config/krx")
    parser.add_argument("--tickers", nargs="+", default=list(DEFAULT_TICKERS))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    client = None
    try:
        calendar = TradingCalendar.from_csv(args.calendar_dir / "open-days.csv", args.calendar_dir / "trading-hours.csv")
        schedule = plan(calendar, args.as_of)
        if args.command == "plan":
            print(json.dumps(schedule))
            return 0
        if not 1 <= len(args.tickers) <= 5 or len(set(args.tickers)) != len(args.tickers):
            raise RiskError("INVALID_OBSERVATION_TICKERS", "관측 종목은 중복 없이 1~5개로 지정하세요.", 400)
        calendar.batch_target(now_kst(), args.as_of)
        output = args.output or ROOT / f"data/close-observation-{args.as_of}.jsonl"
        if not output.resolve().is_relative_to((ROOT / "data").resolve()) or output.suffix != ".jsonl":
            raise RiskError("INVALID_OBSERVATION_PATH", "관측 파일은 data 아래의 JSONL로 지정하세요.", 400)
        settings = Settings.from_env()
        universe = load_universe(settings.universe)
        if any(t not in universe or universe[t].status != "active" for t in args.tickers):
            raise RiskError("INVALID_OBSERVATION_TICKERS", "활성화된 종목만 관측할 수 있습니다.", 400)
        with batch_lock(output.with_suffix(".lock")):
            previous = read_observations(output)
            client = KisClient(settings.kis_key, settings.kis_secret, settings.kis_environment)
            records = observe(client, calendar, args.as_of, args.tickers, settings.kis_environment, previous=previous)
            with output.open("a", encoding="utf-8") as stream:
                for record in records:
                    stream.write(json.dumps(record) + "\n")
            print(json.dumps({"records": records, "provider_finality": "NOT_ESTABLISHED"}))
            return int(any(r["status"] != "ROW_PRESENT" for r in records))
    except RiskError as exc:
        print(json.dumps({"error": {"code": exc.code, "message": exc.message}}, ensure_ascii=True))
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
