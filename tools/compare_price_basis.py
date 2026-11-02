"""Read-only price-basis evidence. No service DB access or automatic decision."""

import argparse
import json
import os
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import unquote

import httpx

from app.clients.kis import KisClient
from app.config import ROOT, Settings, read_env
from app.domain import KST, RiskError, now_kst
from app.services.calendar import TradingCalendar

TICKERS = {"005930": "stock", "247540": "stock", "069500": "etf", "102110": "etf"}
ENDPOINTS = {
    "stock": "https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2",
    "etf": "https://apis.data.go.kr/1160100/GetSecuritiesProductInfoService_V2/getETFPriceInfo_V2",
}


def public_row(body, ticker, target):
    response = body["response"]
    if str(response["header"]["resultCode"]) not in {"00", "0"}:
        raise ValueError("provider error")
    data = response["body"]
    if int(data["totalCount"]) == 0:
        return None
    items = data["items"]["item"]
    items = [items] if isinstance(items, dict) else items
    rows = [r for r in items if r["basDt"] == target.strftime("%Y%m%d")
            and str(r["srtnCd"]).removeprefix("A") == ticker]
    if len(rows) != 1 or int(data["totalCount"]) != 1:
        raise ValueError("ambiguous response")
    row = rows[0]
    close, volume = Decimal(str(row["clpr"])), int(row["trqu"])
    if not close.is_finite() or close <= 0 or volume < 0:
        raise ValueError("invalid price")
    return {"close": str(close), "volume": volume, "basis": "PUBLIC_CLPR_ADJUSTMENT_UNCONFIRMED"}


def comparison(left, right):
    if left is None or right is None:
        return "MISSING"
    return "MATCH" if Decimal(left["close"]) == Decimal(right["close"]) else "DIFFERENT_REVIEW_BASIS"


def baseline_rows(path, target):
    """Normalize the existing weekend evidence without rewriting it."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("environment") != "demo":
        raise ValueError("baseline environment mismatch")
    records = {}
    for record in data["records"]:
        if record.get("adjustment") != "0":
            continue
        for row in record["rows"]:
            if row["stck_bsop_date"] == target.strftime("%Y%m%d"):
                value = {"close": row["stck_clpr"], "volume": int(row["acml_vol"])}
                old = records.get(record["ticker"])
                if old is not None and old != value:
                    raise ValueError("baseline repeats differ")
                records[record["ticker"]] = value
    return records


def next_session(calendar, target):
    return next(d for d in calendar.sessions if d > target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "kis", "public", "compare"))
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 10, 30))
    parser.add_argument("--kis-file", type=Path)
    parser.add_argument("--public-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline-file", type=Path, help="Optional existing weekend evidence")
    args = parser.parse_args()
    client = None
    try:
        output = args.output.resolve() if args.output else None
        if output and (not output.is_relative_to((ROOT / "data").resolve()) or output.suffix != ".json" or output.exists()):
            raise ValueError("output must be a new data/*.json file")
        calendar = TradingCalendar.from_csv(ROOT / "config/krx/open-days.csv", ROOT / "config/krx/trading-hours.csv")
        if args.as_of not in calendar.sessions:
            raise ValueError("target must be a session")
        available = datetime.combine(next_session(calendar, args.as_of), time(13), KST)
        if args.command == "plan":
            result = {"target": str(args.as_of), "tickers": TICKERS,
                      "public_not_before": available.isoformat(), "network_calls": 0,
                      "provider_finality": "NOT_ESTABLISHED"}
        elif args.command == "compare":
            left = json.loads(args.kis_file.read_text(encoding="utf-8"))
            right = json.loads(args.public_file.read_text(encoding="utf-8"))
            if left["target"] != str(args.as_of) or right["target"] != str(args.as_of) or left["source"] != "kis" or right["source"] != "public":
                raise ValueError("incompatible evidence")
            result = {"target": str(args.as_of), "comparison": {
                t: comparison(left["records"].get(t), right["records"].get(t)) for t in TICKERS},
                "provider_finality": "NOT_ESTABLISHED", "decision": "MANUAL_REVIEW_REQUIRED"}
        else:
            now = now_kst()
            if now.date() < available.date() or (args.command == "public" and now < available):
                raise ValueError("comparison window has not opened")
            result = {"target": str(args.as_of), "source": args.command,
                      "observed_at": now.isoformat(), "records": {}, "provider_finality": "NOT_ESTABLISHED"}
            if args.command == "kis":
                settings = Settings.from_env()
                if settings.kis_environment != "demo":
                    raise ValueError("this verification requires demo")
                client = KisClient(settings.kis_key, settings.kis_secret, "demo")
                result["environment"] = "demo"
                for ticker in TICKERS:
                    prices = client.history(ticker, args.as_of, args.as_of)
                    if len(prices) > 1 or any(p.day != args.as_of for p in prices):
                        raise ValueError("unexpected date")
                    result["records"][ticker] = ({"close": str(prices[0].close), "volume": prices[0].volume,
                                                       "basis": "KIS_ADJUSTED_0"} if prices else None)
                if args.baseline_file:
                    baseline = baseline_rows(args.baseline_file, args.as_of)
                    result["baseline_file"] = args.baseline_file.name
                    result["baseline_comparison"] = {t: comparison(baseline.get(t), result["records"][t]) for t in TICKERS}
            else:
                key = os.environ.get("DATA_GO_KR_SERVICE_KEY") or read_env(ROOT / ".env", allowed={"DATA_GO_KR_SERVICE_KEY"}).get("DATA_GO_KR_SERVICE_KEY")
                if not key:
                    raise ValueError("DATA_GO_KR_SERVICE_KEY missing")
                with httpx.Client(timeout=30, follow_redirects=False) as http:
                    for ticker, kind in TICKERS.items():
                        response = http.get(ENDPOINTS[kind], params={"serviceKey": unquote(key), "resultType": "json",
                            "numOfRows": 10, "pageNo": 1, "basDt": args.as_of.strftime("%Y%m%d"), "likeSrtnCd": ticker})
                        response.raise_for_status()
                        result["records"][ticker] = public_row(response.json(), ticker, args.as_of)
        if output:
            with output.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (RiskError, OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration, InvalidOperation, httpx.HTTPError):
        # URLs and provider bodies can contain credentials. Never print exceptions.
        print('{"error":"PRICE_BASIS_CHECK_FAILED","hint":"Check date, credentials, response schema and unused output path; no service data changed."}')
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
