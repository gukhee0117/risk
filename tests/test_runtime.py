from dataclasses import replace
from datetime import datetime, timedelta
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient

from app.domain import KST, RiskError
from app.main import create_app
from app.services.batch import batch_lock
from app.services.runtime import BatchRuntime, start_scheduler
from conftest import make_prices


def runtime_for(market, *, failures=None, clock=None):
    cal, now, sessions, repo, universe, settings = market
    now = cal.closes[sessions[-1]] + timedelta(minutes=10)
    state = {'now': now, 'calls': [], 'failures': failures or {}, 'constructed': 0, 'closed': 0}
    prices = make_prices(sessions)

    class Client:
        def __init__(self, *_):
            state['constructed'] += 1
        def history(self, ticker, *_):
            state['calls'].append(ticker)
            code = state['failures'].get(ticker)
            if code:
                raise RiskError(code, 'synthetic failure')
            return prices
        def close(self):
            state['closed'] += 1

    runtime = BatchRuntime(settings, clock=clock or (lambda: state['now']), client_factory=Client)
    return runtime, state, settings


def test_first_tick_and_restart_skip_success_without_kis(market):
    runtime, state, settings = runtime_for(market)
    result = runtime.tick()
    assert set(result['updated']) == {'005930','000660'}
    assert state['closed'] == 1
    assert runtime.tick()['status'] == 'NO_PENDING_ITEMS'
    restarted = BatchRuntime(settings, clock=runtime.clock, client_factory=runtime.client_factory)
    assert restarted.tick()['status'] == 'NO_PENDING_ITEMS'
    assert state['constructed'] == 1
    assert list((settings.database.parent/'backup').glob('risk-*.sqlite3'))


def test_retry_only_failed_item_and_quality_failure_does_not_loop(market):
    runtime, state, _ = runtime_for(market, failures={'000660': 'KIS_UNAVAILABLE'})
    runtime.tick()
    state['calls'].clear()
    state['failures'] = {'000660':'INSUFFICIENT_SAMPLES'}
    assert runtime.tick()['failed'] == [{'ticker':'000660','code':'INSUFFICIENT_SAMPLES'}]
    assert state['calls'] == ['000660']
    assert runtime.tick()['status'] == 'NO_PENDING_ITEMS'
    assert state['calls'] == ['000660']
    assert runtime.repository.health()['lastBatchStatus'] == 'PARTIAL'


def test_retry_limit_survives_restart(market):
    runtime, state, settings = runtime_for(market, failures={'000660':'LATEST_PRICE_MISSING'})
    for _ in range(3):
        runtime.tick()
        runtime = BatchRuntime(settings, clock=runtime.clock, client_factory=runtime.client_factory)
    assert runtime.tick()['status'] == 'NO_PENDING_ITEMS'
    assert state['calls'].count('000660') == 3
    assert state['calls'].count('005930') == 1


def test_auth_failure_does_not_retry_or_start_pending_tickers(market):
    runtime, state, _ = runtime_for(market, failures={'005930':'KIS_AUTH_FAILED'})
    runtime.tick()
    assert runtime.tick()['status'] == 'NO_PENDING_ITEMS'
    assert state['calls'] == ['005930']


def test_missing_calendar_keeps_api_and_no_kis(market):
    runtime, state, settings = runtime_for(market)
    runtime.settings = replace(settings, calendar=None)
    assert runtime.tick()['code'] == 'CALENDAR_UNAVAILABLE'
    assert state['constructed'] == 0
    with TestClient(create_app(runtime.settings, clock=runtime.clock, runtime=runtime)) as api:
        response = api.get('/health')
        assert response.status_code == 200 and response.json()['status'] == 'DEGRADED'


def test_stop_keeps_success_checkpoint_and_restart_only_collects_unfinished(market):
    runtime, state, settings = runtime_for(market)
    original_factory=runtime.client_factory
    class StoppingClient(original_factory):
        def history(self,ticker,*args):
            values=super().history(ticker,*args)
            runtime.stop()
            return values
    runtime.client_factory=StoppingClient
    result=runtime.tick()
    assert result['status'] == 'INTERRUPTED' and result['updated'] == ['005930']
    assert runtime.repository.completed(market[2][-1]) == {'005930'}
    state['calls'].clear()
    restarted=BatchRuntime(settings,clock=runtime.clock,client_factory=original_factory)
    assert restarted.tick()['updated'] == ['000660']
    assert state['calls'] == ['000660']


def test_overlapping_ticks_do_not_construct_two_clients(market):
    runtime, state, _ = runtime_for(market)
    runtime._execution.acquire()
    try:
        assert runtime.tick()['status'] == 'BUSY_OR_STOPPED'
        assert runtime.batch_running() is True
        assert state['constructed'] == 0
    finally:
        runtime._execution.release()


def test_health_and_grades_answer_while_batch_waits_on_kis(market):
    runtime, state, settings = runtime_for(market)
    runtime.tick()  # previous results exist
    state['now'] += timedelta(days=1)
    entered, release = Event(), Event()
    class WaitingClient:
        def __init__(self, *_): pass
        def history(self, *_):
            entered.set()
            assert release.wait(5)
            raise RiskError('KIS_UNAVAILABLE','simulated timeout')
        def close(self): pass
    runtime.client_factory = WaitingClient
    thread = Thread(target=runtime.tick)
    thread.start()
    try:
        assert entered.wait(5)
        with TestClient(create_app(settings, clock=runtime.clock, runtime=runtime),
                        headers={'X-Risk-Service-Key': settings.service_key}) as api:
            body = api.get('/health').json()
            assert body['batch_running'] is True
            assert len(api.get('/grades').json()) == 2
            release.set()
            thread.join(5)
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert runtime.repository.health()['lastBatchStatus'] == 'FAILED'


def test_backup_failure_retries_backup_only(market, monkeypatch):
    import app.services.runtime as module
    runtime, state, settings = runtime_for(market)
    real_backup = module.daily_backup
    def failure(*_):
        raise RiskError('BACKUP_FAILED','synthetic failure')
    monkeypatch.setattr(module,'daily_backup',failure)
    assert runtime.tick()['code'] == 'BACKUP_FAILED'
    assert runtime.last_error == 'BACKUP_FAILED'
    calls=list(state['calls'])
    monkeypatch.setattr(module,'daily_backup',real_backup)
    assert runtime.tick()['status'] == 'NO_PENDING_ITEMS'
    assert runtime.last_error is None and state['calls'] == calls
    assert list((settings.database.parent/'backup').glob('risk-*.sqlite3'))


def test_health_detects_manual_batch_lock(market):
    runtime, _, settings = runtime_for(market)
    with TestClient(create_app(settings, clock=runtime.clock, runtime=runtime)) as api:
        with batch_lock(settings.database.with_suffix('.batch.lock')):
            assert api.get('/health').json()['batch_running'] is True
        assert api.get('/health').json()['batch_running'] is False


def test_health_does_not_show_abandoned_run_as_live_or_healthy(market):
    runtime,_,settings=runtime_for(market)
    runtime.tick()
    runtime.repository.start_run(market[2][-1],'synthetic','resume',['005930'],runtime.clock())
    with TestClient(create_app(settings,clock=runtime.clock,runtime=runtime)) as api:
        body=api.get('/health').json()
        assert body['batch_running'] is False
        assert body['lastBatchStatus'] == 'INTERRUPTED'
        assert body['status'] == 'DEGRADED'


def test_startup_and_shutdown_own_scheduler_only_when_enabled(market):
    runtime, state, settings = runtime_for(market)
    events = []
    class Scheduler:
        def shutdown(self, *, wait):
            events.append(('shutdown',wait))
    def factory(received):
        assert received is runtime
        events.append('start')
        return Scheduler()
    with TestClient(create_app(replace(settings,scheduler_enabled=True), runtime=runtime, scheduler_factory=factory)):
        assert events == ['start']
    assert events == ['start',('shutdown',True)]
    assert runtime.tick()['status'] == 'BUSY_OR_STOPPED'
    assert state['constructed'] == 0


def test_actual_scheduler_has_seoul_clock_one_executor_and_no_overlap(market):
    runtime, _, _ = runtime_for(market)
    runtime.stop()  # real scheduler must never issue a network call in this test
    scheduler = start_scheduler(runtime)
    try:
        assert str(scheduler.timezone) == 'Asia/Seoul'
        job = scheduler.get_job('risk-batch')
        assert job.max_instances == 1 and job.coalesce is True
        assert scheduler._executors['default']._pool._max_workers == 1
    finally:
        scheduler.shutdown(wait=True)


def test_real_lifespan_scheduler_recovers_immediately_without_blocking_api(market):
    runtime,state,settings=runtime_for(market)
    completed=Event()
    original=runtime.client_factory
    class Client(original):
        def close(self):
            super().close()
            completed.set()
    runtime.client_factory=Client
    with TestClient(create_app(replace(settings,scheduler_enabled=True),runtime=runtime,clock=runtime.clock),
                    headers={'X-Risk-Service-Key':settings.service_key}) as api:
        assert api.get('/health').status_code == 200
        assert completed.wait(5)
        assert len(api.get('/grades').json()) == 2
        assert state['constructed'] == 1


def test_disabled_lifespan_never_starts_scheduler(market):
    runtime,_,settings=runtime_for(market)
    def forbidden(_):
        pytest.fail('Disabled scheduler must not be started')
    with TestClient(create_app(settings,runtime=runtime,scheduler_factory=forbidden)) as api:
        assert api.get('/health').status_code == 200


def test_latest_eligible_session_handles_morning_weekend_and_changed_close(market):
    cal, _, sessions, *_ = market
    target = sessions[-1]
    close = cal.closes[target]
    assert cal.latest_batch_target(close+timedelta(minutes=9)) == sessions[-2]
    assert cal.latest_batch_target(close+timedelta(minutes=10)) == target
    assert cal.latest_batch_target(close.replace(hour=10)) == sessions[-2]
    # Synthetic fixture has 16:00 close; it is not an assertion about a real KRX date.
    assert close.hour == 16
    friday = next(d for d in cal.sessions if d > target and d.weekday() == 4)
    saturday = datetime.combine(friday+timedelta(days=1), datetime.min.time(), KST).replace(hour=12)
    assert cal.latest_batch_target(saturday) == friday
