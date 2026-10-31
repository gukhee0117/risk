from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Event

import pytest
from fastapi.testclient import TestClient

from app.domain import RiskError
from app.main import create_app
from app.repositories.sqlite import Repository
from app.services.backup import daily_backup
from app.services.calculation import calculate_grade
from conftest import make_prices


@pytest.mark.parametrize('path,method', [('/grades','GET'),('/package/volatility','POST')])
def test_auth_failure_does_not_echo_secret(market, path, method):
    _, now, _, _, _, settings = market
    with TestClient(create_app(settings,clock=lambda:now)) as api:
        for headers in ({},{'X-Risk-Service-Key':'wrong-secret'}):
            response = api.request(method,path,headers=headers,json={'items':[]})
            assert response.status_code == 401
            assert 'wrong-secret' not in response.text and settings.service_key not in response.text
        assert api.get('/health').status_code == 200


def test_unconfigured_auth_fails_closed_but_health_lives(market):
    _, now, _, _, _, settings = market
    with TestClient(create_app(replace(settings,service_key=None),clock=lambda:now)) as api:
        assert api.get('/grades').status_code == 503
        response = api.get('/health')
        assert response.status_code == 200 and response.json()['status'] == 'DEGRADED'
        assert response.json()['lastSuccessfulCalculationAt'] is None


def test_last_success_time_includes_unchanged_calculation(market):
    cal, now, sessions, repo, universe, settings = market
    from app.services.batch import run_batch
    prices = make_prices(sessions)
    class Client:
        def history(self,*_): return prices
    options = dict(target_date=sessions[-1],clock=lambda:now)
    run_batch(repo,cal,universe,Client(),now,**options)
    later = now+timedelta(minutes=10)
    run_batch(repo,cal,universe,Client(),later,target_date=sessions[-1],clock=lambda:later,reanalyze=True)
    assert repo.health()['lastSuccessfulCalculationAt'] == later.isoformat()
    with repo.snapshot() as db:
        assert all(g.as_of == now and g.analysis_version == 1 for g in repo.grades(db).values())


def test_wal_reader_and_live_backup_see_only_committed_snapshot(market,tmp_path):
    _,now,sessions,repo,*_ = market
    prices = make_prices(sessions)
    grade = calculate_grade('005930',prices,sessions,now)
    repo.replace(grade,prices)
    with repo.connect() as writer:
        assert writer.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        writer.execute('BEGIN IMMEDIATE')
        writer.execute("UPDATE risk_grade SET var_975=.9 WHERE ticker='005930'")
        with repo.snapshot() as reader:
            assert repo.grades(reader)['005930'] == grade
        destination = tmp_path/'while-writing.sqlite3'
        repo.backup(destination)
        with Repository(destination).snapshot() as db:
            assert repo.grades(db)['005930'] == grade
        writer.rollback()


def test_writer_connections_are_serialized_across_repository_instances(market):
    repo = market[3]
    attempted, acquired = Event(), Event()
    def other_writer():
        attempted.set()
        with Repository(repo.path).connect():
            acquired.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with repo.connect():
            future = pool.submit(other_writer)
            assert attempted.wait(2)
            assert not acquired.wait(.1)
            with repo.snapshot() as db:
                db.execute('SELECT 1')
        future.result(timeout=3)
    assert acquired.is_set()


def test_daily_backups_keep_seven_and_replace_same_day_atomically(market,tmp_path):
    _,now,sessions,repo,*_ = market
    prices = make_prices(sessions)
    grade = calculate_grade('005930',prices,sessions,now)
    repo.replace(grade,prices)
    root = tmp_path/'backups'
    root.mkdir()
    unrelated=root/'keep-me.sqlite3'
    unrelated.write_bytes(b'untouched')
    target=sessions[-1]
    for offset in range(8):
        daily_backup(repo,target+timedelta(days=offset),root)
    assert len(list(root.glob('risk-*.sqlite3'))) == 7
    assert not (root/f'risk-{target}.sqlite3').exists()
    assert unrelated.read_bytes() == b'untouched'
    latest=target+timedelta(days=7)
    changed,_=repo.replace(replace(grade,var975=.6),prices)
    destination=daily_backup(repo,latest,root)
    with Repository(destination).snapshot() as db:
        assert repo.grades(db)['005930'] == changed
        assert db.execute('SELECT COUNT(*) FROM analysis_publication').fetchone()[0] == 2


def test_failed_daily_backup_preserves_previous_file(market,tmp_path,monkeypatch):
    repo=market[3]
    target=market[2][-1]
    destination=daily_backup(repo,target,tmp_path)
    before=destination.read_bytes()
    run_id=repo.start_run(target,'synthetic','resume',[],market[1])
    repo.finish_run(run_id,'FAILED',market[1])
    def failure(path):
        path.write_bytes(b'partial')
        raise RiskError('STORAGE_UNAVAILABLE','synthetic failure')
    monkeypatch.setattr(repo,'backup',failure)
    with pytest.raises(RiskError):
        daily_backup(repo,target,tmp_path)
    assert destination.read_bytes() == before
    assert not list(tmp_path.glob('.risk-backup-*.sqlite3'))
