from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import Path

from app.domain import RiskError, now_kst
from app.services.calculation import calculate_grade


@contextmanager
def batch_lock(path: Path):
    """Local filesystem advisory lock; the OS releases it even after a kill.

    Keep the file: unlinking a locked inode can create two independent locks.
    Shared/multi-host volume behavior must be verified before deployment.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b"\x00")
                handle.flush()
            handle.seek(0)
            lock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            unlock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            lock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            unlock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        try:
            lock()
        except OSError as exc:
            raise RiskError("BATCH_ALREADY_RUNNING", "배치 잠금을 확보할 수 없습니다.", 503) from exc
        try:
            yield
        finally:
            handle.seek(0)
            unlock()
    finally:
        handle.close()


def run_batch(repository, calendar, universe, client, now, *, target_date=None, reanalyze=False, clock=now_kst,
              stop_requested=lambda: False):
    end = calendar.batch_target(now, target_date)
    result = {"priceTo": end.isoformat() if end else None, "updated": [], "unchanged": [], "failed": [], "skipped": []}
    if end is None:
        result["status"] = "NOT_DUE"
        return result
    sessions = calendar.window(end)
    with batch_lock(repository.path.with_suffix(".batch.lock")):
        repository.initialize()
        done = set() if reanalyze else repository.completed(end)
        pending = [t for t, i in universe.items() if i.status == "active" and t not in done]
        run_id = repository.start_run(end, calendar.version, "reanalyze" if reanalyze else "resume", pending, now)
        result["runId"] = run_id
        for ticker, instrument in universe.items():
            if stop_requested():
                repository.finish_run(run_id, "INTERRUPTED", clock())
                result["status"] = "INTERRUPTED"
                return result
            if instrument.status != "active":
                result["skipped"].append({"ticker": ticker, "code": instrument.status})
                continue
            if ticker in done:
                result["skipped"].append({"ticker": ticker, "code": "already_completed"})
                continue
            item_id = repository.start_item(run_id, ticker, clock())
            try:
                prices = client.history(ticker, sessions[0], end)
                if any(p.day not in sessions for p in prices):
                    raise RiskError("INVALID_KIS_DATA", "공식 거래일 범위 밖 가격이 있습니다.", tickers=[ticker])
                # Never persist a partial refresh or splice different adjusted-price vintages.
                grade = calculate_grade(ticker, prices, sessions, clock())
                grade = replace(grade, as_of=clock())
                _, changed = repository.replace(grade, prices, item_id=item_id)
                result["updated" if changed else "unchanged"].append(ticker)
            except RiskError as exc:
                repository.fail_item(item_id, exc.code, clock())
                result["failed"].append({"ticker": ticker, "code": exc.code})
                if exc.code in {"KIS_CONFIGURATION", "KIS_AUTH_FAILED", "KIS_RESPONSE_ERROR", "KIS_QUERY_FAILED", "STORAGE_UNAVAILABLE", "INVALID_BATCH_STATE"}:
                    repository.finish_run(run_id, "FAILED", clock())
                    break
            except Exception:
                repository.fail_item(item_id, "CODE_ERROR", clock())
                repository.finish_run(run_id, "FAILED", clock())
                raise
        repository.finish_run(run_id, "FAILED" if result["failed"] else "SUCCEEDED", clock())
    return result
