"""Local configuration/storage diagnostics; no provider calls or live DB writes."""

import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from app.domain import KST, RiskError
from app.repositories import migrations
from app.repositories.sqlite import Repository
from app.services.backup import signature
from app.services.calendar import TradingCalendar
from app.universe import load_universe


def inspect_database(path, *, require_wal=False):
    with Repository(path).snapshot() as db:
        version, _ = migrations.inspect(db)
        if version != migrations.VERSION:
            raise RiskError('MIGRATION_REQUIRED', '명시적 보존 마이그레이션이 필요합니다.', 503)
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {'price_daily', 'risk_grade', 'analysis_publication', 'price_correction',
                    'batch_run', 'batch_item'}
        if not required <= tables:
            raise RiskError('SCHEMA_MISMATCH', '필수 저장 구조를 확인하세요.', 503)
        if db.execute('PRAGMA quick_check').fetchall()[0][0] != 'ok':
            raise RiskError('INTEGRITY_CHECK_FAILED', '저장소 무결성을 확인하세요.', 503)
        if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise RiskError('INTEGRITY_CHECK_FAILED', '저장소 참조 무결성을 확인하세요.', 503)
        mode = db.execute('PRAGMA journal_mode').fetchone()[0].lower()
        if require_wal and mode != 'wal':
            raise RiskError('WAL_UNAVAILABLE', '운영 저장소의 WAL 설정을 확인하세요.', 503)
        return Repository.grades(db)


def probe_directory(directory):
    """Touch only a unique temporary child, never the configured database."""
    if not directory.is_dir():
        raise RiskError('STORAGE_DIRECTORY_MISSING', '대상 디렉터리를 먼저 준비하세요.', 503)
    with tempfile.TemporaryDirectory(prefix='.risk-preflight-', dir=directory) as temporary:
        path = Path(temporary) / 'probe.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            if db.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower() != 'wal':
                raise RiskError('WAL_UNAVAILABLE', '임시 저장소에서 WAL을 사용할 수 없습니다.', 503)
            db.execute('CREATE TABLE probe (value INTEGER)')
            db.execute('INSERT INTO probe VALUES (1)')
            db.commit()
            if db.execute('SELECT value FROM probe').fetchone()[0] != 1:
                raise RiskError('STORAGE_PROBE_FAILED', '임시 저장소 검사가 실패했습니다.', 503)
    return {'freeBytes': shutil.disk_usage(directory).free}


def preflight(settings, now, *, probe_storage=False):
    checks = []

    def add(name, status, code, **details):
        checks.append({'name': name, 'status': status, 'code': code, **details})

    missing = [name for name, value in (
        ('RISK_SERVICE_KEY', settings.service_key), ('KIS_APP_KEY', settings.kis_key),
        ('KIS_APP_SECRET', settings.kis_secret)) if not value or not value.strip()]
    invalid_key = settings.service_key and any(c in settings.service_key for c in '\r\n')
    add('credentials', 'FAIL' if missing or invalid_key else 'PASS',
        'INVALID_CREDENTIAL_CONFIGURATION' if missing or invalid_key else 'CONFIGURED',
        missingVariables=missing)
    add('kis_environment', 'PASS' if settings.kis_environment in {'demo', 'real'} else 'FAIL',
        'CONFIGURED' if settings.kis_environment in {'demo', 'real'} else 'INVALID_KIS_ENVIRONMENT')
    add('scheduler', 'PASS', 'ENABLED' if settings.scheduler_enabled else 'DISABLED')

    calendar, reference, universe = None, None, {}
    try:
        if settings.calendar is None:
            raise RiskError('CALENDAR_UNAVAILABLE', '공식 달력 설정이 필요합니다.', 503)
        calendar = TradingCalendar.from_csv(settings.calendar, settings.trading_hours)
        reference = calendar.latest(now)
        calendar.window(reference)
        # Check recovery eligibility separately from API market-date readiness.
        calendar.latest_batch_target(now)
        add('calendar', 'PASS', 'AVAILABLE', referenceDate=str(reference),
            calendarVersion=calendar.version, coverageEnd=str(calendar.days[-1][0]))
        unknown = [str(day) for day in calendar.sessions
                   if day >= now.astimezone(KST).date() and calendar.closes[day] is None]
        if unknown:
            add('calendar_special_sessions', 'WARN', 'UNCONFIRMED_HOURS', dates=unknown)
    except RiskError as exc:
        add('calendar', 'FAIL', exc.code)

    try:
        universe = load_universe(settings.universe)
        active = sum(i.status == 'active' for i in universe.values())
        add('universe', 'PASS' if active else 'FAIL', 'AVAILABLE' if active else 'NO_ACTIVE_INSTRUMENTS',
            totalCount=len(universe), activeCount=active, excludedCount=len(universe) - active)
    except RiskError as exc:
        add('universe', 'FAIL', exc.code)

    try:
        if settings.database.exists() and not settings.database.is_file():
            add('database', 'FAIL', 'INVALID_DATABASE_PATH')
        elif not settings.database.is_file():
            add('database', 'WARN', 'INITIAL_BATCH_REQUIRED')
        else:
            grades = inspect_database(settings.database, require_wal=True)
            add('database', 'PASS', 'AVAILABLE', schemaVersion=migrations.VERSION,
                journalMode='wal', publishedCount=len(grades))
            if reference is not None and calendar is not None:
                usable = sum(calendar.fresh(g.price_to, reference) and ticker in universe
                             and universe[ticker].status == 'active' for ticker, g in grades.items())
                add('published_results', 'PASS' if usable else 'WARN',
                    'AVAILABLE' if usable else 'NO_USABLE_RESULTS', usableCount=usable)
    except (RiskError, OSError, ValueError, TypeError) as exc:
        code = exc.code if isinstance(exc, RiskError) else (
            'STORAGE_UNAVAILABLE' if isinstance(exc, OSError) else 'INVALID_STORED_RESULT')
        add('database', 'FAIL', code)

    backup_dir = settings.backup_dir or settings.database.parent / 'backup'
    try:
        backups = sorted(p for p in backup_dir.iterdir()
                         if re.fullmatch(r'risk-\d{4}-\d{2}-\d{2}\.sqlite3', p.name) and p.is_file()) if backup_dir.exists() else []
        if not backups:
            add('backup', 'WARN', 'BACKUP_NOT_CREATED')
        else:
            inspect_database(backups[-1])
            matches = settings.database.is_file() and signature(Repository(backups[-1])) == signature(Repository(settings.database))
            add('backup', 'PASS' if matches else 'WARN', 'CURRENT' if matches else 'BACKUP_NOT_CURRENT',
                snapshotDate=backups[-1].name[5:-8], retainedCount=len(backups))
    except (RiskError, OSError, ValueError, TypeError):
        add('backup', 'FAIL', 'BACKUP_UNREADABLE')

    if probe_storage:
        # A backup directory may be created by the first batch. Probe its parent
        # without creating that configured directory or changing permissions.
        targets = {'database_directory': settings.database.parent, 'backup_directory': backup_dir}
        for name, directory in targets.items():
            try:
                if name == 'backup_directory' and not directory.exists():
                    directory = directory.parent
                add(name, 'PASS', 'TEMPORARY_WAL_PROBE_PASSED', **probe_directory(directory))
            except (RiskError, OSError, sqlite3.Error) as exc:
                add(name, 'FAIL', exc.code if isinstance(exc, RiskError) else 'STORAGE_PROBE_FAILED')
    else:
        add('storage_permissions', 'WARN', 'STORAGE_PROBE_NOT_RUN')

    status = 'BLOCKED' if any(c['status'] == 'FAIL' for c in checks) else (
        'REVIEW_REQUIRED' if any(c['status'] == 'WARN' for c in checks) else 'PASS')
    return {'scope': 'LOCAL_CONFIGURATION_AND_STORAGE', 'checkedAt': now.isoformat(),
            'status': status, 'checks': checks,
            'manualChecks': ['local_filesystem_and_persistent_volume', 'one_container_one_worker',
                             'deployment_shared_lock', 'off_host_backup_and_restore',
                             'maria_shared_key_calendar_and_instrument_match'],
            'deploymentApproved': False}
