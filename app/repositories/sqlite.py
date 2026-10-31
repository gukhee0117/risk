"""Atomic per-instrument snapshots and consistent read/backup operations."""

import sqlite3
import hashlib
import json
import os
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.domain import Grade, Price, RiskError
from app.repositories import migrations

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_daily (
 ticker TEXT NOT NULL, trade_date TEXT NOT NULL, close_price TEXT NOT NULL,
 volume INTEGER NOT NULL CHECK(volume >= 0), PRIMARY KEY(ticker, trade_date)
);
CREATE TABLE IF NOT EXISTS risk_grade (
 ticker TEXT PRIMARY KEY, var_975 REAL NOT NULL, grade INTEGER NOT NULL CHECK(grade BETWEEN 1 AND 6),
 volatility REAL NOT NULL, price_from TEXT NOT NULL, price_to TEXT NOT NULL, calculated_at TEXT NOT NULL
);
"""

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def writer_lock(path):
    # One live writer connection per file/process; reads use separate connections.
    key = str(path.resolve()).casefold() if os.name == 'nt' else str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


class Repository:
    def __init__(self, path: Path):
        self.path = Path(path)

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version, existing = migrations.inspect(db)
            if version != migrations.VERSION and existing:
                raise RiskError("MIGRATION_REQUIRED", "기존 DB를 백업하는 migrate 명령을 먼저 실행하세요.", 503)
            self._enable_wal(db)
            if version == migrations.VERSION:
                return
            db.execute("BEGIN IMMEDIATE")
            migrations.upgrade(db, SCHEMA)

    @staticmethod
    def _enable_wal(db):
        if db.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != "wal":
            raise RiskError("WAL_UNAVAILABLE", "SQLite WAL을 사용할 수 없습니다.", 503)

    def migrate(self):
        """Invoke with batch lock held and service writers stopped; backup first."""
        if not self.path.is_file():
            self.initialize()
            return None
        with self.snapshot() as db:
            version, existing = migrations.inspect(db)
        if version == migrations.VERSION:
            return None
        backup = self.path.with_name(self.path.name + f".pre-v1-{uuid4().hex}.sqlite3") if existing else None
        if backup is not None:
            self.backup(backup)
        with self.connect() as db:
            self._enable_wal(db)
            db.execute("BEGIN IMMEDIATE")
            migrations.inspect(db)
            migrations.upgrade(db, SCHEMA)
            for grade in self.grades(db).values():
                db.execute("INSERT INTO analysis_publication VALUES (?,?,?,?,?)", (
                    grade.ticker, grade.analysis_version, grade.price_to.isoformat(), grade.as_of.isoformat(),
                    self.result_hash(grade, self.prices(db, grade.ticker)),
                ))
        return backup

    @contextmanager
    def connect(self, *, write=True):
        from contextlib import nullcontext
        guard = writer_lock(self.path) if write else nullcontext()
        with guard:
            with self._connection(write) as db:
                yield db

    @contextmanager
    def _connection(self, write):
        try:
            db = (sqlite3.connect(self.path, timeout=10) if write else
                  sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", timeout=10, uri=True))
            db.execute("PRAGMA foreign_keys=ON")
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()
        except sqlite3.Error as exc:
            raise RiskError("STORAGE_UNAVAILABLE", "SQLite 저장소에 접근할 수 없습니다.", 503) from exc

    @contextmanager
    def snapshot(self):
        with self.connect(write=False) as db:
            db.execute("BEGIN")
            yield db

    @staticmethod
    def result_hash(grade, prices):
        def decimal_text(value):
            text = format(value, "f")
            return text.rstrip("0").rstrip(".") if "." in text else text
        # Raw inputs count: corrected prices can affect a package even if the
        # displayed single-stock grade/volatility are numerically unchanged.
        payload = ["historical-var-v1", grade.ticker, grade.price_from.isoformat(), grade.price_to.isoformat(),
                   grade.var975, grade.risk_grade, grade.volatility,
                   [(p.day.isoformat(), decimal_text(p.close), p.volume) for p in sorted(prices, key=lambda p: p.day)]]
        return hashlib.sha256(json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

    def replace(self, grade: Grade, prices: list[Price], *, item_id=None):
        digest = self.result_hash(grade, prices)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = self.grades(db).get(grade.ticker)
            if old is not None and grade.price_to < old.price_to:
                raise RiskError("OLDER_REFERENCE_DATE", "과거 기준일로 최신 결과를 덮어쓸 수 없습니다.")
            prior = db.execute("SELECT analysis_version,result_hash FROM analysis_publication WHERE ticker=? ORDER BY analysis_version DESC LIMIT 1", (grade.ticker,)).fetchone()
            if old is not None and prior is not None and prior["result_hash"] == digest:
                self._complete_item(db, item_id, old, "UNCHANGED", grade.as_of)
                return old, False
            version = max(old.analysis_version if old else 0, prior["analysis_version"] if prior else 0) + 1
            published = replace(grade, analysis_version=version)
            previous = {p.day: p for p in self.prices(db, grade.ticker)}
            for p in prices:
                if p.day in previous and p.close != previous[p.day].close:
                    db.execute("INSERT INTO price_correction VALUES (?,?,?,?,?,?,?)", (
                        grade.ticker, p.day.isoformat(), str(previous[p.day].close), str(p.close),
                        old.analysis_version if old else 0, version, grade.as_of.isoformat(),
                    ))
            db.execute("DELETE FROM price_daily WHERE ticker = ?", (grade.ticker,))
            db.executemany("INSERT INTO price_daily (ticker,trade_date,close_price,volume,analysis_version) VALUES (?, ?, ?, ?, ?)", [
                (grade.ticker, p.day.isoformat(), str(p.close), p.volume, version) for p in prices
            ])
            db.execute("INSERT OR REPLACE INTO risk_grade VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
                grade.ticker, grade.var975, grade.risk_grade, grade.volatility,
                grade.price_from.isoformat(), grade.price_to.isoformat(), grade.as_of.isoformat(), version,
            ))
            db.execute("INSERT INTO analysis_publication VALUES (?,?,?,?,?)", (
                grade.ticker, version, grade.price_to.isoformat(), grade.as_of.isoformat(), digest,
            ))
            self._complete_item(db, item_id, published, "SUCCEEDED", grade.as_of)
            return published, True

    @staticmethod
    def _complete_item(db, item_id, grade, status, finished):
        if item_id is not None:
            cursor = db.execute("""UPDATE batch_item SET status=?,analysis_version=?,finished_at=?
                WHERE item_id=? AND status='RUNNING' AND ticker=? AND run_id IN
                (SELECT run_id FROM batch_run WHERE target_date=?)""", (
                status, grade.analysis_version, finished.isoformat(), item_id, grade.ticker, grade.price_to.isoformat(),
            ))
            if cursor.rowcount != 1:
                raise RiskError("INVALID_BATCH_STATE", "배치 처리 상태를 확인하세요.", 503)

    @staticmethod
    def grades(db) -> dict[str, Grade]:
        return {row["ticker"]: Grade(
            row["ticker"], row["var_975"], row["grade"], row["volatility"],
            date.fromisoformat(row["price_from"]), date.fromisoformat(row["price_to"]),
            datetime.fromisoformat(row["calculated_at"]), row["analysis_version"],
        ) for row in db.execute("SELECT ticker, var_975, grade, volatility, price_from, price_to, calculated_at, analysis_version FROM risk_grade ORDER BY ticker")}

    def completed(self, target):
        with self.snapshot() as db:
            return {r[0] for r in db.execute("""SELECT i.ticker FROM batch_item i
                JOIN batch_run r ON r.run_id=i.run_id JOIN risk_grade g ON g.ticker=i.ticker
                WHERE r.target_date=? AND g.price_to=r.target_date
                AND i.item_id=(SELECT MAX(last.item_id) FROM batch_item last
                    JOIN batch_run lr ON lr.run_id=last.run_id
                    WHERE last.ticker=i.ticker AND lr.target_date=r.target_date)
                AND i.status IN ('SUCCEEDED','UNCHANGED') AND i.analysis_version=g.analysis_version""",
                (target.isoformat(),))}

    def start_run(self, target, calendar_version, mode, tickers, now):
        run_id = uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Safe only while holding the exclusive batch lock for this DB.
            db.execute("UPDATE batch_item SET status='INTERRUPTED',finished_at=? WHERE status='RUNNING'", (now.isoformat(),))
            db.execute("UPDATE batch_run SET status='INTERRUPTED',finished_at=? WHERE status='RUNNING'", (now.isoformat(),))
            db.execute("INSERT INTO batch_run VALUES (?,?,?,?,?,?,?)", (
                run_id, target.isoformat(), calendar_version, mode, now.isoformat(), None, "RUNNING",
            ))
            db.executemany("INSERT INTO batch_item(run_id,ticker,status) VALUES (?,?,'PENDING')", [(run_id, t) for t in tickers])
        return run_id

    def start_item(self, run_id, ticker, now):
        with self.connect() as db:
            db.execute("UPDATE batch_item SET status='RUNNING',started_at=? WHERE run_id=? AND ticker=? AND status='PENDING'", (now.isoformat(), run_id, ticker))
            return db.execute("SELECT item_id FROM batch_item WHERE run_id=? AND ticker=?", (run_id, ticker)).fetchone()[0]

    def fail_item(self, item_id, code, now):
        with self.connect() as db:
            db.execute("UPDATE batch_item SET status='FAILED',error_code=?,finished_at=? WHERE item_id=?", (code, now.isoformat(), item_id))

    def finish_run(self, run_id, status, now):
        with self.connect() as db:
            db.execute("UPDATE batch_run SET status=?,finished_at=? WHERE run_id=?", (status, now.isoformat(), run_id))

    @staticmethod
    def prices(db, ticker: str) -> list[Price]:
        return [Price(date.fromisoformat(row["trade_date"]), Decimal(row["close_price"]), row["volume"])
                for row in db.execute("SELECT trade_date, close_price, volume FROM price_daily WHERE ticker=? ORDER BY trade_date", (ticker,))]

    def backup(self, destination: Path):
        if self.path.resolve() == destination.resolve() or destination.exists():
            raise RiskError("INVALID_BACKUP_PATH", "새 백업 파일 경로를 지정하세요.", 400)
        # Exclusive creation prevents racing backups from overwriting a file.
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("xb"):
                pass
        except FileExistsError as exc:
            raise RiskError("INVALID_BACKUP_PATH", "새 백업 파일 경로를 지정하세요.", 400) from exc
        try:
            with self.connect(write=False) as source:
                target = sqlite3.connect(destination)
                try:
                    source.backup(target)
                finally:
                    target.close()
        except BaseException:
            # This path was exclusively created above, not a pre-existing backup.
            destination.unlink(missing_ok=True)
            raise

    def automatic_candidates(self, target, tickers, maximum_attempts):
        retryable = {"KIS_UNAVAILABLE", "LATEST_PRICE_MISSING"}
        fatal = {"KIS_CONFIGURATION", "KIS_AUTH_FAILED", "KIS_RESPONSE_ERROR", "KIS_QUERY_FAILED", "CODE_ERROR"}
        completed = self.completed(target)
        with self.snapshot() as db:
            rows = db.execute("""SELECT i.* FROM batch_item i JOIN batch_run r USING(run_id)
                WHERE r.target_date=? ORDER BY i.item_id""", (target.isoformat(),)).fetchall()
        latest, attempts = {}, {}
        for row in rows:
            latest[row['ticker']] = row
            if row['started_at'] is not None:
                attempts[row['ticker']] = attempts.get(row['ticker'], 0) + 1
        if any(t in tickers and r['status'] == 'FAILED' and r['error_code'] in fatal for t, r in latest.items()):
            return []
        eligible = []
        for ticker in tickers:
            row = latest.get(ticker)
            if ticker in completed or attempts.get(ticker, 0) >= maximum_attempts:
                continue
            if row is not None and row['status'] == 'FAILED' and row['error_code'] not in retryable:
                continue
            eligible.append(ticker)
        return eligible

    def health(self):
        with self.snapshot() as db:
            migrations.inspect(db)
            run = db.execute("SELECT * FROM batch_run ORDER BY started_at DESC,rowid DESC LIMIT 1").fetchone()
            last_success = db.execute("""SELECT MAX(finished_at) FROM batch_item
                WHERE status IN ('SUCCEEDED','UNCHANGED')""").fetchone()[0]
            if last_success is None:
                last_success = db.execute("SELECT MAX(calculated_at) FROM risk_grade").fetchone()[0]
            status = run['status'] if run else None
            if run and status != 'RUNNING':
                states = db.execute("""SELECT i.status FROM batch_item i JOIN batch_run r USING(run_id)
                    WHERE r.target_date=? AND i.item_id=(SELECT MAX(j.item_id) FROM batch_item j
                    JOIN batch_run s ON s.run_id=j.run_id WHERE s.target_date=r.target_date AND j.ticker=i.ticker)""",
                    (run['target_date'],)).fetchall()
                successes = sum(r[0] in {'SUCCEEDED','UNCHANGED'} for r in states)
                if successes and any(r[0] not in {'SUCCEEDED','UNCHANGED'} for r in states):
                    status = 'PARTIAL'
            return {"lastSuccessfulCalculationAt": last_success, "lastBatchStatus": status}
