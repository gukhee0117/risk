"""Single-process scheduled batch orchestration; durable checkpoints stay in SQLite."""

import logging
import threading
from zoneinfo import ZoneInfo

from app.clients.kis import KisClient
from app.domain import RiskError, now_kst
from app.repositories.sqlite import Repository
from app.services.batch import batch_lock, run_batch
from app.services.backup import daily_backup
from app.services.calendar import TradingCalendar
from app.universe import load_universe

logger = logging.getLogger(__name__)


class BatchRuntime:
    def __init__(self, settings, *, clock=now_kst, client_factory=KisClient):
        self.settings, self.clock, self.client_factory = settings, clock, client_factory
        self.repository = Repository(settings.database)
        self._execution = threading.Lock()
        self._stopping = threading.Event()
        self.last_error = None

    def stop(self):
        self._stopping.set()

    def batch_running(self):
        if self._execution.locked():
            return True
        path = self.repository.path.with_suffix('.batch.lock')
        if not path.exists():
            return False
        try:
            with batch_lock(path):
                return False
        except RiskError as exc:
            if exc.code == 'BATCH_ALREADY_RUNNING':
                return True
            raise
        except OSError as exc:
            raise RiskError('STORAGE_UNAVAILABLE', '배치 잠금 상태를 확인할 수 없습니다.', 503) from exc

    def tick(self):
        if self._stopping.is_set() or not self._execution.acquire(blocking=False):
            return {"status": "BUSY_OR_STOPPED"}
        client = None
        try:
            settings = self.settings
            if not settings.service_key:
                raise RiskError('AUTH_CONFIGURATION', '서비스 인증 설정이 필요합니다.', 503)
            if settings.calendar is None:
                raise RiskError("CALENDAR_UNAVAILABLE", "공식 달력 설정이 필요합니다.", 503)
            calendar = TradingCalendar.from_csv(settings.calendar, settings.trading_hours)
            now = self.clock()
            target = calendar.latest_batch_target(now)
            if target is None:
                return {"status": "NOT_DUE"}
            calendar.window(target)
            universe = load_universe(settings.universe)
            with batch_lock(self.repository.path.with_suffix('.batch.lock')):
                self.repository.initialize()
            active = [t for t, i in universe.items() if i.status == 'active']
            eligible = self.repository.automatic_candidates(target, active, settings.batch_max_attempts)
            if not eligible:
                # A prior backup failure must not rerun KIS or disappear silently.
                daily_backup(self.repository, target, settings.backup_dir, settings.backup_keep)
                self.last_error = None
                return {"status": "NO_PENDING_ITEMS", "priceTo": str(target)}
            client = self.client_factory(settings.kis_key, settings.kis_secret, settings.kis_environment)
            result = run_batch(self.repository, calendar, {t: universe[t] for t in eligible},
                               client, now, target_date=target, clock=self.clock, stop_requested=self._stopping.is_set)
            daily_backup(self.repository, target, settings.backup_dir, settings.backup_keep)
            self.last_error = None
            return result
        except RiskError as exc:
            self.last_error = exc.code
            logger.warning("Risk scheduled batch: %s", exc.code)
            return {"status": "ERROR", "code": exc.code}
        except Exception:
            # Never log provider payloads, headers or exception strings.
            self.last_error = 'CODE_ERROR'
            logger.error("Risk scheduled batch: CODE_ERROR")
            return {"status": "ERROR", "code": "CODE_ERROR"}
        finally:
            try:
                if client is not None:
                    client.close()
            finally:
                self._execution.release()


def start_scheduler(runtime):
    from apscheduler.executors.pool import ThreadPoolExecutor
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler(timezone=ZoneInfo('Asia/Seoul'),
                                    executors={'default': ThreadPoolExecutor(1)},
                                    job_defaults={'max_instances': 1, 'coalesce': True})
    # Poll calendar eligibility, not a fixed 15:40 cron: changed-close days work too.
    scheduler.add_job(runtime.tick, 'interval', seconds=runtime.settings.scheduler_poll_seconds,
                      id='risk-batch', next_run_time=now_kst())
    scheduler.start()
    return scheduler
