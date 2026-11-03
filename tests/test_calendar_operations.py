import csv
from datetime import date, datetime, timedelta

import pytest

from app import cli
from app.domain import KST, RiskError
from app.services.batch import run_batch
from app.services.calendar import TradingCalendar


def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        writer.writerows(rows)


def test_official_dates_and_hours_are_separate_and_changed_close_is_respected(tmp_path):
    dates, hours = tmp_path / 'dates.csv', tmp_path / 'hours.csv'
    write_csv(dates, ['date','is_open'], [('2026-10-28',1),('2026-10-29',1),('2026-10-30',0)])
    # Synthetic delayed-close case, not an assertion about the real date.
    write_csv(hours, ['date','regular_close_at','available_at','source_ref'], [
        ('2026-10-28','2026-10-28T15:30:00+09:00','','synthetic'),
        ('2026-10-29','2026-10-29T16:30:00+09:00','2026-10-29T18:00:00+09:00','synthetic'),
    ])
    cal = TradingCalendar.from_csv(dates, hours)
    at = lambda h,m: datetime(2026,10,29,h,m,tzinfo=KST)
    assert cal.latest(at(16,29)) == date(2026,10,28)
    assert cal.latest(at(16,30)) == date(2026,10,29)
    assert cal.batch_target(at(16,39)) is None
    assert cal.batch_target(at(16,40)) == date(2026,10,29)
    # Provider metadata does not control expiry or pretend to prove finality.
    assert cal.available[date(2026,10,29)] == at(18,0)
    assert cal.batch_target(datetime(2026,10,30,17,tzinfo=KST)) is None


def test_missing_hours_never_falls_back_to_available_at_or_weekday(tmp_path):
    path = tmp_path / 'legacy.csv'
    write_csv(path, ['date','is_open','available_at'], [
        ('2026-10-28',1,'2026-10-28T15:40:00+09:00'),
        ('2026-10-29',1,'2026-10-29T15:40:00+09:00'),
    ])
    cal = TradingCalendar.from_csv(path)
    now = datetime(2026,10,29,17,tzinfo=KST)
    for action in (lambda:cal.latest(now), lambda:cal.batch_target(now)):
        with pytest.raises(RiskError) as error:
            action()
        assert error.value.code == 'TRADING_HOURS_UNAVAILABLE'
    with pytest.raises(RiskError) as error:
        cal.latest(now + timedelta(days=1))
    assert error.value.code == 'CALENDAR_OUT_OF_RANGE'


@pytest.mark.parametrize('stamp,source', [
    ('2026-10-29T15:30:00','synthetic'),
    ('2026-10-28T15:30:00+09:00','synthetic'),
    ('2026-10-29T15:30:00+09:00',''),
])
def test_invalid_or_unsourced_hours_fail_closed(tmp_path, stamp, source):
    path = tmp_path / 'bad.csv'
    write_csv(path,['date','is_open','regular_close_at','source_ref'], [('2026-10-29',1,stamp,source)])
    with pytest.raises(RiskError):
        TradingCalendar.from_csv(path)


def test_not_due_never_recalculates_previous_day_or_touches_client(market):
    cal, now, _, repo, universe, _ = market
    class Client:
        def history(self, *args):
            pytest.fail('not due: no network call allowed')
    assert run_batch(repo, cal, universe, Client(), now)['status'] == 'NOT_DUE'
    with repo.snapshot() as db:
        assert db.execute('SELECT count(*) FROM batch_run').fetchone()[0] == 0


def test_current_stale_expired_boundary_uses_completed_sessions(market):
    cal, _, sessions, *_ = market
    end = sessions[-1]
    ix = cal.sessions.index(end)
    for lag, expected in [(0,'CURRENT'),(3,'STALE'),(4,'EXPIRED')]:
        assert cal.state(end, cal.sessions[ix+lag]) == expected


def test_cli_reanalysis_requires_explicit_date_and_calendar_status_has_no_kis(market, monkeypatch, capsys):
    import json
    _, now, _, repo, _, settings = market
    monkeypatch.setenv('RISK_CALENDAR_PATH',str(settings.calendar))
    monkeypatch.setenv('RISK_DB_PATH',str(repo.path))
    monkeypatch.setattr(cli,'now_kst',lambda:now)
    def forbidden(*args): pytest.fail('must not construct KIS client')
    monkeypatch.setattr(cli,'KisClient',forbidden)
    monkeypatch.setattr('sys.argv',['risk','batch','--reanalyze'])
    assert cli.main() == 1
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'INVALID_BATCH_DATE'
    monkeypatch.setattr('sys.argv',['risk','calendar-status'])
    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result['calendarVersion']) == 64
    assert result['referenceDate'] < now.date().isoformat()


def test_separate_hours_config_does_not_need_shared_keys(tmp_path, monkeypatch):
    from app.config import ROOT, Settings
    monkeypatch.setenv('RISK_TRADING_HOURS_PATH','config/hours.csv')
    assert Settings.from_env().trading_hours == ROOT / 'config/hours.csv'
