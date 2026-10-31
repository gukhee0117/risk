"""Preflight must diagnose failures without changing live data or calling KIS."""

from dataclasses import replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from app import cli
from app.domain import KST
from app.repositories.sqlite import Repository, SCHEMA
from app.services.backup import daily_backup
from app.services.calculation import calculate_grade
from app.services.preflight import preflight
from conftest import make_prices


def checks(result):
    return {row['name']: row for row in result['checks']}


def prepared(market):
    _, now, sessions, repo, _, settings = market
    prices = make_prices(sessions)
    repo.replace(calculate_grade('005930', prices, sessions, now), prices)
    daily_backup(repo, sessions[-1])
    return replace(settings, kis_key='private-test-key', kis_secret='private-test-secret')


def test_complete_probe_preserves_source_and_backup_and_cleans_temporary_files(market):
    settings = prepared(market)
    source_before = settings.database.read_bytes()
    backup = next((settings.database.parent / 'backup').glob('risk-*.sqlite3'))
    backup_before = backup.read_bytes()
    result = preflight(settings, market[1], probe_storage=True)
    assert result['status'] == 'PASS'
    assert checks(result)['backup']['code'] == 'CURRENT'
    assert checks(result)['published_results']['usableCount'] == 1
    assert checks(result)['backup']['snapshotDate'] == str(market[2][-1])
    assert result['deploymentApproved'] is False and result['manualChecks']
    assert settings.database.read_bytes() == source_before
    assert backup.read_bytes() == backup_before
    assert not list(settings.database.parent.rglob('.risk-preflight-*'))
    wire = json.dumps(result)
    for secret in (settings.kis_key, settings.kis_secret, settings.service_key):
        assert secret not in wire


def test_missing_credentials_and_database_do_not_create_real_paths(market):
    settings = replace(market[-1], database=market[-1].database.parent / 'uncreated' / 'prices.db',
                       service_key=None, kis_key=None, kis_secret=None)
    result = preflight(settings, market[1])
    assert result['status'] == 'BLOCKED'
    assert checks(result)['credentials']['missingVariables'] == [
        'RISK_SERVICE_KEY', 'KIS_APP_KEY', 'KIS_APP_SECRET']
    assert checks(result)['database']['code'] == 'INITIAL_BATCH_REQUIRED'
    assert checks(result)['storage_permissions']['code'] == 'STORAGE_PROBE_NOT_RUN'
    assert not settings.database.parent.exists()


@pytest.mark.parametrize('lag,expected,usable', [(3, 'PASS', 1), (4, 'WARN', 0)])
def test_expiry_is_reported_without_refreshing_stored_data(market, lag, expected, usable):
    settings = prepared(market)
    cal, _, sessions, repo, *_ = market
    reference = cal.sessions[cal.sessions.index(sessions[-1]) + lag]
    now = cal.closes[reference]
    before = settings.database.read_bytes()
    result = preflight(settings, now)
    assert checks(result)['published_results']['status'] == expected
    assert checks(result)['published_results']['usableCount'] == usable
    assert settings.database.read_bytes() == before


def test_unconfirmed_session_is_blocked_and_does_not_use_regular_hour_fallback(market):
    root = Path(__file__).resolve().parents[1]
    settings = replace(prepared(market), calendar=root / 'config/krx/open-days.csv',
                       trading_hours=root / 'config/krx/trading-hours.csv')
    result = preflight(settings, datetime(2026, 11, 19, 17, tzinfo=KST))
    assert checks(result)['calendar']['code'] == 'TRADING_HOURS_UNAVAILABLE'
    assert result['status'] == 'BLOCKED'
    result = preflight(settings, datetime(2026, 11, 2, 17, tzinfo=KST))
    assert checks(result)['calendar']['status'] == 'PASS'
    assert checks(result)['calendar_special_sessions']['dates'] == ['2026-11-19']


@pytest.mark.parametrize('legacy', [False, True])
def test_corrupt_or_legacy_database_is_not_repaired_or_migrated(market, legacy):
    settings = prepared(market)
    target = settings.database.parent / 'incompatible.sqlite3'
    if legacy:
        db = sqlite3.connect(target)
        try:
            db.executescript(SCHEMA)
        finally:
            db.close()
    else:
        target.write_bytes(b'not-a-database')
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    result = preflight(replace(settings, database=target), market[1])
    assert result['status'] == 'BLOCKED'
    assert checks(result)['database']['code'] == ('MIGRATION_REQUIRED' if legacy else 'STORAGE_UNAVAILABLE')
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before


def test_broken_backup_is_reported_and_preserved(market):
    settings = prepared(market)
    backup = next((settings.database.parent / 'backup').glob('risk-*.sqlite3'))
    backup.write_bytes(b'invalid-backup')
    result = preflight(settings, market[1])
    assert checks(result)['backup']['code'] == 'BACKUP_UNREADABLE'
    assert backup.read_bytes() == b'invalid-backup'
    assert checks(result)['database']['status'] == 'PASS'


def test_storage_permission_failure_is_reported_without_changing_live_database(market, monkeypatch):
    settings = prepared(market)
    def denied(*args, **kwargs):
        raise PermissionError('must-not-appear-in-output')
    monkeypatch.setattr('app.services.preflight.tempfile.TemporaryDirectory', denied)
    result = preflight(settings, market[1], probe_storage=True)
    assert checks(result)['database_directory']['code'] == 'STORAGE_PROBE_FAILED'
    assert result['status'] == 'BLOCKED'
    assert 'must-not-appear-in-output' not in json.dumps(result)


def test_cli_preflight_never_constructs_a_provider_client(market, monkeypatch, capsys):
    settings = prepared(market)
    monkeypatch.setattr(cli.Settings, 'from_env', lambda: settings)
    monkeypatch.setattr(cli, 'now_kst', lambda: market[1])
    def forbidden(*args):
        pytest.fail('preflight must not construct a KIS client')
    monkeypatch.setattr(cli, 'KisClient', forbidden)
    monkeypatch.setattr('sys.argv', ['risk', 'preflight', '--probe-storage'])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'PASS'


def test_database_without_wal_is_reported_without_changing_its_mode(market):
    settings = prepared(market)
    repo = Repository(settings.database)
    with repo.connect() as db:
        assert db.execute('PRAGMA journal_mode=DELETE').fetchone()[0] == 'delete'
    result = preflight(settings, market[1])
    assert checks(result)['database']['code'] == 'WAL_UNAVAILABLE'
    with repo.snapshot() as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'


def test_storage_probe_does_not_create_missing_configured_directories(market):
    settings = replace(prepared(market), database=market[-1].database.parent / 'missing' / 'prices.db')
    result = preflight(settings, market[1], probe_storage=True)
    assert checks(result)['database_directory']['code'] == 'STORAGE_DIRECTORY_MISSING'
    assert not settings.database.parent.exists()


def test_preflight_reads_committed_results_while_batch_writer_is_active(market):
    settings = prepared(market)
    repo = Repository(settings.database)
    with repo.connect() as writer:
        writer.execute('BEGIN IMMEDIATE')
        writer.execute('DELETE FROM risk_grade')
        result = preflight(settings, market[1], probe_storage=True)
        assert checks(result)['published_results']['usableCount'] == 1
        assert checks(result)['backup']['code'] == 'CURRENT'
        writer.rollback()


def test_invalid_header_and_provider_environment_are_rejected_without_echoing_values(market):
    settings = replace(prepared(market), service_key='private-header\r\nvalue',
                       kis_environment='private-invalid-environment')
    result = preflight(settings, market[1])
    assert checks(result)['credentials']['status'] == 'FAIL'
    assert checks(result)['kis_environment']['code'] == 'INVALID_KIS_ENVIRONMENT'
    assert settings.service_key not in json.dumps(result)
    assert settings.kis_environment not in json.dumps(result)


def test_inaccessible_database_path_reports_failure_without_traceback(market, monkeypatch):
    settings = prepared(market)
    original = Path.exists
    def inaccessible(path):
        if path == settings.database:
            raise PermissionError('private-path-error')
        return original(path)
    monkeypatch.setattr(Path, 'exists', inaccessible)
    result = preflight(settings, market[1], probe_storage=True)
    assert checks(result)['database']['code'] == 'STORAGE_UNAVAILABLE'
    assert result['status'] == 'BLOCKED'
    assert 'private-path-error' not in json.dumps(result)
