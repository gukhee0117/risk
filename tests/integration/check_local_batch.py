"""Offline checks of a completed live-batch database; never calls KIS."""

import argparse
import json
import math
import statistics
import secrets
from dataclasses import replace
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain import RiskError, now_kst
from app.main import create_app
from app.repositories.sqlite import Repository
from app.services.batch import run_batch
from app.services.calendar import TradingCalendar
from app.universe import load_universe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calendar-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    measured = json.loads((args.output_dir / "report.json").read_text(encoding="utf-8"))
    assert "error" not in measured, "Batch did not complete normally"
    target = date.fromisoformat(measured["target"])
    key = secrets.token_urlsafe(32)
    settings = replace(Settings.from_env(), database=args.output_dir / "risk.sqlite3", service_key=key,
                       calendar=args.calendar_dir / "open-days.csv",
                       trading_hours=args.calendar_dir / "trading-hours.csv")
    calendar = TradingCalendar.from_csv(settings.calendar, settings.trading_hours)
    assert calendar.version == measured["calendar_version"], "Use the exact batch calendar"
    repository, universe = Repository(settings.database), load_universe(settings.universe)
    sessions = calendar.window(target)
    successful = set(measured["result"]["updated"] + measured["result"]["unchanged"])
    with repository.snapshot() as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        grades = repository.grades(db)
        assert set(grades) == successful
        price_rows = db.execute("SELECT count(*) FROM price_daily").fetchone()[0]
        assert db.execute("SELECT count(*) FROM analysis_publication").fetchone()[0] == len(grades)
        assert all(g.analysis_version == 1 and g.price_to == target for g in grades.values())
        valid_returns = {}
        for ticker, grade in grades.items():
            prices = repository.prices(db, ticker)
            by_day = {p.day: p for p in prices}
            values = [float(by_day[b].close / by_day[a].close - 1)
                      for a, b in zip(sessions, sessions[1:])
                      if a in by_day and b in by_day and by_day[a].volume and by_day[b].volume]
            valid_returns[ticker] = len(values)
            vol = statistics.stdev(values) * math.sqrt(250)
            ordered = sorted(values)
            position = (len(ordered) - 1) * .025
            low = math.floor(position)
            quantile = ordered[low] + (ordered[math.ceil(position)] - ordered[low]) * (position - low)
            var = abs(quantile) * math.sqrt(250)
            expected_grade = next((n for n, cap in ((6,.01),(5,.1),(4,.2),(3,.3),(2,.5)) if var <= cap), 1)
            assert math.isclose(grade.volatility, vol, rel_tol=1e-12, abs_tol=1e-12), ticker
            assert math.isclose(grade.var975, var, rel_tol=1e-12, abs_tol=1e-12), ticker
            assert grade.risk_grade == expected_grade, ticker
        failed = {row["ticker"]: row["code"] for row in measured["result"]["failed"]}
        assert not failed.keys() & grades.keys()
        for ticker in failed:
            assert not repository.prices(db, ticker), "Failed initial collection persisted partial prices"
    with TestClient(create_app(settings), headers={'X-Risk-Service-Key': key}) as api:
        response = api.get("/grades")
        assert response.status_code == 200
        payload = response.json()
        assert {r["ticker"] for r in payload} == successful
        assert all(r["priceTo"] == str(target) and r["analysisVersion"] == 1 for r in payload)
        if payload:
            chosen = payload[0]
            response = api.post("/package/volatility", json={"items": [{"ticker": chosen["ticker"], "weight": 1}]})
            assert response.status_code == 200
            assert response.json()["volatility"] == chosen["volatility"]
        for ticker in failed:
            response = api.post("/package/volatility", json={"items": [{"ticker": ticker, "weight": 1}]})
            assert response.status_code == 422
    class ForbiddenClient:
        def history(self, *_):
            raise AssertionError("Successful resume must not call KIS")
    resumed = run_batch(repository, calendar, {t: universe[t] for t in successful},
                        ForbiddenClient(), now_kst(), target_date=target)
    assert not resumed["updated"] and not resumed["failed"] and len(resumed["skipped"]) == len(successful)
    # Inject a transport failure into a separate backup, never the measured DB.
    if successful:
        chosen_ticker = sorted(successful)[0]
        original = grades[chosen_ticker]
        with repository.snapshot() as db:
            original_prices = repository.prices(db, chosen_ticker)
        backup_path = args.output_dir / "recovery-check.sqlite3"
        repository.backup(backup_path)
        backup = Repository(backup_path)
        selected = {chosen_ticker: universe[chosen_ticker]}
        class FailingClient:
            def history(self, *_):
                raise RiskError("KIS_UNAVAILABLE", "Simulated transport failure")
        failure = run_batch(backup, calendar, selected, FailingClient(), now_kst(),
                            target_date=target, reanalyze=True)
        assert failure["failed"] == [{"ticker": chosen_ticker, "code": "KIS_UNAVAILABLE"}]
        with backup.snapshot() as db:
            assert backup.grades(db)[chosen_ticker] == original
            assert backup.prices(db, chosen_ticker) == original_prices
        class CachedClient:
            def history(self, *_):
                return original_prices
        recovered = run_batch(backup, calendar, selected, CachedClient(), now_kst(), target_date=target)
        assert recovered["unchanged"] == [chosen_ticker] and not recovered["failed"]
        with backup.snapshot() as db:
            assert backup.grades(db)[chosen_ticker] == original
    report = {"sqlite_integrity": "ok", "published_stocks": len(grades), "price_rows": price_rows,
              "independent_calculation_checks": len(grades), "valid_return_counts": valid_returns,
              "api_checks": "GET /grades, single-stock package, failed-stock package (in-process ASGI HTTP)",
              "resume_successful_without_kis": len(resumed["skipped"]), "failed_initial_data_not_persisted": failed,
              "failure_preservation_and_resume": "simulated KIS failure on separate live-data backup; unchanged recovery keeps version"}
    (args.output_dir / "checks.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
