from dataclasses import replace

import pytest

from app.domain import RiskError
from app.repositories.sqlite import Repository
from app.services.batch import batch_lock, run_batch
from app.services.calculation import calculate_grade
from conftest import make_prices


def test_atomic_rollback_and_idempotent_replace(market):
    _, now, sessions, repository, *_ = market
    prices = make_prices(sessions)
    grade = calculate_grade("005930", prices, sessions, now)
    repository.replace(grade, prices)
    repository.replace(grade, prices)
    with pytest.raises(RiskError):
        repository.replace(replace(grade, var975=.9), prices + [prices[0]])
    with repository.snapshot() as db:
        assert repository.grades(db)["005930"] == grade
        assert repository.prices(db, "005930") == prices
        assert db.execute("SELECT count(*) FROM risk_grade").fetchone()[0] == 1


def test_batch_failure_preserves_old_series_success_updates(market):
    calendar, now, sessions, repository, universe, _ = market
    prices = make_prices(sessions)
    old = calculate_grade("005930", prices, sessions, now)
    repository.replace(old, prices)

    class Client:
        def history(self, ticker, start, end):
            if ticker == "005930":
                raise RiskError("TEST_UPSTREAM_FAILURE", "synthetic error")
            return prices

    result = run_batch(repository, calendar, universe, Client(), now, target_date=sessions[-1], clock=lambda: now)
    assert result["updated"] == ["000660"]
    assert result["failed"] == [{"ticker": "005930", "code": "TEST_UPSTREAM_FAILURE"}]
    with repository.snapshot() as db:
        assert repository.grades(db)["005930"] == old
        assert repository.prices(db, "005930") == prices
        assert set(repository.grades(db)) == {"005930", "000660"}


def test_batch_lock_rejects_concurrent_writer_and_releases(tmp_path):
    path = tmp_path / "batch.lock"
    with batch_lock(path):
        with pytest.raises(RiskError, match="잠금"):
            with batch_lock(path):
                pytest.fail("second batch must not start")
    # The inert file stays; the OS lock has been released and can be reacquired.
    with batch_lock(path):
        pass


def test_sqlite_backup_is_consistent(market, tmp_path):
    _, now, sessions, repository, *_ = market
    prices = make_prices(sessions)
    grade = calculate_grade("005930", prices, sessions, now)
    repository.replace(grade, prices)
    target = tmp_path / "backup.sqlite3"
    repository.backup(target)
    backup = Repository(target)
    with backup.snapshot() as db:
        assert backup.grades(db)["005930"] == grade
        assert backup.prices(db, "005930") == prices
    with pytest.raises(RiskError):
        repository.backup(target)


def test_reader_never_sees_uncommitted_grade_price_mix(market):
    _, now, sessions, repository, *_ = market
    prices = make_prices(sessions)
    grade = calculate_grade("005930", prices, sessions, now)
    repository.replace(grade, prices)
    with repository.connect() as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE risk_grade SET var_975=.9 WHERE ticker='005930'")
        writer.execute("DELETE FROM price_daily WHERE ticker='005930'")
        with repository.snapshot() as reader:
            assert repository.grades(reader)["005930"] == grade
            assert repository.prices(reader, "005930") == prices
        writer.rollback()


def test_incompatible_schema_is_rejected_before_mutation(tmp_path):
    repository = Repository(tmp_path / "old.sqlite3")
    with repository.connect() as db:
        db.execute("CREATE TABLE price_daily (ticker TEXT)")
    with pytest.raises(RiskError) as error:
        repository.initialize()
    assert error.value.code == "SCHEMA_MISMATCH"
    with repository.connect() as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='risk_grade'").fetchone() is None
