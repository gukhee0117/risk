"""Real SQLite transactions, synthetic prices, and an actual killed lock owner."""

import subprocess
import sys
import os
import signal
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app.domain import RiskError
from app.repositories import migrations
from app.repositories.sqlite import Repository, SCHEMA
from app.services.batch import batch_lock, run_batch
from app.services.calculation import calculate_grade
from conftest import make_prices


def legacy_repository(path, grade, prices):
    repo = Repository(path)
    with repo.connect() as db:
        db.executescript(SCHEMA)
        db.executemany("INSERT INTO price_daily VALUES (?,?,?,?)", [
            (grade.ticker, p.day.isoformat(), str(p.close), p.volume) for p in prices
        ])
        db.execute("INSERT INTO risk_grade VALUES (?,?,?,?,?,?,?)", (
            grade.ticker, grade.var975, grade.risk_grade, grade.volatility,
            grade.price_from.isoformat(), grade.price_to.isoformat(), grade.as_of.isoformat(),
        ))
    return repo


def test_migration_preserves_values_backup_and_monotonic_versions(market, tmp_path):
    _, now, sessions, _, _, _ = market
    prices = make_prices(sessions)
    grade = calculate_grade("005930", prices, sessions, now)
    repo = legacy_repository(tmp_path / "legacy.sqlite3", grade, prices)
    with pytest.raises(RiskError) as error:
        repo.initialize()
    assert error.value.code == "MIGRATION_REQUIRED"
    backup = repo.migrate()
    assert backup.is_file()
    with Repository(backup).connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        assert len(db.execute("PRAGMA table_info(price_daily)").fetchall()) == 4
    with repo.snapshot() as db:
        assert repo.grades(db)[grade.ticker] == grade
        assert repo.prices(db, grade.ticker) == prices
    assert repo.migrate() is None
    for version in (2, 3):
        # Recreate repository: version allocation must never rely on process memory.
        repo = Repository(repo.path)
        repo.initialize()
        prices = [replace(p, close=p.close + Decimal(version)) if i == 100 else p for i, p in enumerate(prices)]
        updated = calculate_grade(grade.ticker, prices, sessions, now + timedelta(minutes=version))
        published, changed = repo.replace(updated, prices)
        assert changed and published.analysis_version == version
        assert repo.migrate() is None
        with repo.snapshot() as db:
            assert {r[0] for r in db.execute("SELECT analysis_version FROM price_daily")} == {version}


def test_migration_failure_rolls_back_ddl_and_preserves_backup(market, tmp_path, monkeypatch):
    _, now, sessions, *_ = market
    prices = make_prices(sessions)
    grade = calculate_grade("005930", prices, sessions, now)
    repo = legacy_repository(tmp_path / "legacy.sqlite3", grade, prices)
    upgrade = migrations.upgrade
    def fail_after_upgrade(db, schema):
        upgrade(db, schema)
        raise RuntimeError("synthetic interrupted migration")
    monkeypatch.setattr(migrations, "upgrade", fail_after_upgrade)
    with pytest.raises(RuntimeError):
        repo.migrate()
    with repo.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        assert len(db.execute("PRAGMA table_info(price_daily)").fetchall()) == 4
        assert db.execute("SELECT count(*) FROM price_daily").fetchone()[0] == len(prices)
        assert db.execute("SELECT name FROM sqlite_master WHERE name='analysis_publication'").fetchone() is None
    assert len(list(tmp_path.glob("legacy.sqlite3.pre-v1-*.sqlite3"))) == 1


def test_unchanged_keeps_version_timestamp_and_decimal_equivalence(market):
    _, now, sessions, repo, *_ = market
    prices = make_prices(sessions)
    grade = calculate_grade("005930", prices, sessions, now)
    repo.replace(grade, prices)
    reordered = [replace(p, close=Decimal(str(p.close) + '0')) if '.' in str(p.close) else replace(p, close=Decimal(str(p.close) + '.0')) for p in reversed(prices)]
    published, changed = repo.replace(replace(grade, as_of=now + timedelta(hours=2)), reordered)
    assert not changed and published == grade
    with repo.snapshot() as db:
        assert db.execute("SELECT count(*) FROM analysis_publication").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM price_correction").fetchone()[0] == 0


def test_correction_is_not_error_and_publication_rollback_is_atomic(market):
    _, now, sessions, repo, *_ = market
    prices = make_prices(sessions)
    grade = calculate_grade("005930", prices, sessions, now)
    repo.replace(grade, prices)
    revised = [replace(p, close=p.close + 1) if i == 100 else p for i,p in enumerate(prices)]
    new_grade = calculate_grade("005930", revised, sessions, now + timedelta(minutes=1))
    # Fail at final item checkpoint, after all price/grade/history writes.
    with pytest.raises(RiskError):
        repo.replace(new_grade, revised, item_id=99999)
    with repo.snapshot() as db:
        assert repo.grades(db)[grade.ticker] == grade
        assert repo.prices(db, grade.ticker) == prices
        assert db.execute("SELECT count(*) FROM price_correction").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM analysis_publication").fetchone()[0] == 1
    published, changed = repo.replace(new_grade, revised)
    assert changed and published.analysis_version == 2
    with repo.snapshot() as db:
        correction = db.execute("SELECT * FROM price_correction").fetchall()
        assert len(correction) == 1
        assert correction[0]["trade_date"] == sessions[100].isoformat()
        assert (correction[0]["from_version"], correction[0]["to_version"]) == (1, 2)


def test_new_and_rolled_off_dates_are_not_corrections(market):
    cal, now, sessions, repo, *_ = market
    prices = make_prices(sessions)
    grade = calculate_grade("005930", prices, sessions, now)
    repo.replace(grade, prices)
    next_day = cal.sessions[cal.sessions.index(sessions[-1]) + 1]
    new_sessions = cal.window(next_day)
    by_day = {p.day:p for p in prices}
    by_day[next_day] = replace(prices[-1], day=next_day, close=prices[-1].close * Decimal('1.01'))
    revised = [by_day[d] for d in new_sessions]
    repo.replace(calculate_grade("005930", revised, new_sessions, now), revised)
    with repo.snapshot() as db:
        assert db.execute("SELECT count(*) FROM price_correction").fetchone()[0] == 0
    with pytest.raises(RiskError) as error:
        repo.replace(grade, prices)
    assert error.value.code == "OLDER_REFERENCE_DATE"


def test_two_writers_allocate_versions_in_sqlite_transaction(market):
    _, now, sessions, repo, *_ = market
    original = make_prices(sessions)
    repo.replace(calculate_grade("005930", original, sessions, now), original)
    def publish(delta):
        values = [replace(p, close=p.close + delta) if i == 100 else p for i,p in enumerate(original)]
        return Repository(repo.path).replace(calculate_grade("005930", values, sessions, now), values)[0].analysis_version
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(publish, (Decimal(1), Decimal(2)))) == [2, 3]


def test_resume_skips_success_and_explicit_reanalysis_only_publishes_changes(market):
    cal, now, sessions, repo, universe, _ = market
    prices = make_prices(sessions)
    calls = []
    class Crash:
        def history(self, ticker, start, end):
            calls.append(ticker)
            if ticker == "000660":
                raise KeyboardInterrupt
            return prices
    options = dict(target_date=sessions[-1], clock=lambda: now)
    with pytest.raises(KeyboardInterrupt):
        run_batch(repo, cal, universe, Crash(), now, **options)
    with repo.snapshot() as db:
        first = repo.grades(db)["005930"]
    class Client:
        def history(self, ticker, start, end):
            calls.append(ticker)
            return prices
    calls.clear()
    result = run_batch(Repository(repo.path), cal, universe, Client(), now, **options)
    assert calls == ["000660"] and result["updated"] == ["000660"]
    with repo.snapshot() as db:
        assert repo.grades(db)["005930"] == first
        assert db.execute("SELECT count(*) FROM batch_item WHERE status='INTERRUPTED'").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM batch_run WHERE status='INTERRUPTED'").fetchone()[0] == 1
    calls.clear()
    result = run_batch(repo, cal, universe, Client(), now + timedelta(minutes=1), reanalyze=True, **options)
    assert calls == list(universe)
    assert result["unchanged"] == list(universe) and not result["updated"]
    with repo.snapshot() as db:
        assert repo.grades(db)["005930"] == first
        assert db.execute("SELECT count(*) FROM analysis_publication").fetchone()[0] == 2


def test_failed_items_only_are_retried_and_error_history_remains(market):
    cal, now, sessions, repo, universe, _ = market
    prices = make_prices(sessions)
    class Client:
        calls = []
        fail = True
        def history(self, ticker, start, end):
            self.calls.append(ticker)
            if self.fail and ticker == "000660":
                raise RiskError("KIS_UNAVAILABLE", "synthetic timeout")
            return prices
    client = Client()
    options = dict(target_date=sessions[-1], clock=lambda: now)
    run_batch(repo, cal, universe, client, now, **options)
    client.calls.clear(); client.fail = False
    run_batch(repo, cal, universe, client, now, **options)
    assert client.calls == ["000660"]
    with repo.snapshot() as db:
        assert db.execute("SELECT error_code FROM batch_item WHERE status='FAILED'").fetchone()[0] == "KIS_UNAVAILABLE"


def test_auth_failure_stops_remaining_requests(market):
    cal, now, sessions, repo, universe, _ = market
    class Client:
        calls = []
        def history(self, ticker, start, end):
            self.calls.append(ticker)
            raise RiskError("KIS_AUTH_FAILED", "synthetic authentication failure")
    client = Client()
    run_batch(repo, cal, universe, client, now, target_date=sessions[-1], clock=lambda: now)
    assert client.calls == ["005930"]
    with repo.snapshot() as db:
        assert db.execute("SELECT count(*) FROM batch_item WHERE status='PENDING'").fetchone()[0] == 1


@pytest.mark.parametrize('interrupt',[False,True])
def test_failed_reanalysis_is_resumed_even_with_an_older_success(market, interrupt):
    cal, now, sessions, repo, universe, _ = market
    prices=make_prices(sessions)
    class Client:
        fail=False
        calls=[]
        def history(self,ticker,start,end):
            self.calls.append(ticker)
            if self.fail and ticker=='000660':
                if interrupt:
                    raise KeyboardInterrupt
                raise RiskError('KIS_UNAVAILABLE','synthetic failure')
            return prices
    client=Client()
    options=dict(target_date=sessions[-1],clock=lambda:now)
    run_batch(repo,cal,universe,client,now,**options)
    client.fail=True
    if interrupt:
        with pytest.raises(KeyboardInterrupt):
            run_batch(repo,cal,universe,client,now,reanalyze=True,**options)
    else:
        run_batch(repo,cal,universe,client,now,reanalyze=True,**options)
    client.fail=False
    client.calls.clear()
    result=run_batch(repo,cal,universe,client,now,**options)
    assert client.calls==['000660']
    assert result['unchanged']==['000660']
    assert result['skipped']==[{'ticker':'005930','code':'already_completed'}]


def test_killed_process_releases_lock_without_removing_file(tmp_path):
    path = tmp_path / "killed.batch.lock"
    script = "from pathlib import Path; import sys,os; from app.services.batch import batch_lock\nwith batch_lock(Path(sys.argv[1])):\n print(os.getpid(), flush=True)\n sys.stdin.read()"
    process = subprocess.Popen([sys.executable, "-B", "-c", script, str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            ready = executor.submit(process.stdout.readline)
            try:
                # Windows venv launchers may have a different PID from the
                # interpreter holding the lock. Terminate the actual owner.
                owner_pid = int(ready.result(timeout=10).strip())
                with pytest.raises(RiskError):
                    with batch_lock(path):
                        pytest.fail("live writer must hold the lock")
            finally:
                if 'owner_pid' in locals():
                    os.kill(owner_pid, signal.SIGTERM)
                else:
                    process.kill()
                process.wait(timeout=5)
        assert path.exists()
        with batch_lock(path):
            pass
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=5)
        for pipe in (process.stdin, process.stdout, process.stderr):
            pipe.close()
