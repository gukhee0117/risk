"""Transactional v0 -> v1 migration. No DROP/replace of existing price data."""

from app.domain import RiskError

VERSION = 1
LEGACY = {
    "price_daily": {"ticker", "trade_date", "close_price", "volume"},
    "risk_grade": {"ticker", "var_975", "grade", "volatility", "price_from", "price_to", "calculated_at"},
}

TABLES = [
    """CREATE TABLE analysis_publication (
      ticker TEXT NOT NULL, analysis_version INTEGER NOT NULL CHECK(analysis_version > 0),
      price_to TEXT NOT NULL, calculated_at TEXT NOT NULL, result_hash TEXT NOT NULL,
      PRIMARY KEY(ticker, analysis_version))""",
    """CREATE TABLE price_correction (
      ticker TEXT NOT NULL, trade_date TEXT NOT NULL, old_close TEXT NOT NULL, new_close TEXT NOT NULL,
      from_version INTEGER NOT NULL, to_version INTEGER NOT NULL, detected_at TEXT NOT NULL,
      PRIMARY KEY(ticker, trade_date, to_version))""",
    """CREATE TABLE batch_run (
      run_id TEXT PRIMARY KEY, target_date TEXT NOT NULL, calendar_version TEXT NOT NULL,
      mode TEXT NOT NULL CHECK(mode IN ('resume','reanalyze')), started_at TEXT NOT NULL,
      finished_at TEXT, status TEXT NOT NULL
      CHECK(status IN ('RUNNING','SUCCEEDED','FAILED','INTERRUPTED')))""",
    """CREATE TABLE batch_item (
      item_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES batch_run(run_id),
      ticker TEXT NOT NULL, started_at TEXT, finished_at TEXT, error_code TEXT,
      analysis_version INTEGER, status TEXT NOT NULL
      CHECK(status IN ('PENDING','RUNNING','SUCCEEDED','UNCHANGED','FAILED','INTERRUPTED')),
      UNIQUE(run_id,ticker))""",
    "CREATE INDEX batch_target ON batch_run(target_date)",
    "CREATE INDEX batch_ticker_status ON batch_item(ticker,status)",
]


def inspect(db):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, VERSION):
        raise RiskError("SCHEMA_MISMATCH", "지원하지 않는 SQLite 스키마 버전입니다.", 503)
    existing = False
    for table, legacy in LEGACY.items():
        columns = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        expected = legacy | ({"analysis_version"} if version == VERSION else set())
        if columns and columns != expected or version == VERSION and not columns:
            raise RiskError("SCHEMA_MISMATCH", "기존 SQLite 스키마가 호환되지 않습니다.", 503)
        existing |= bool(columns)
    return version, existing


def upgrade(db, legacy_schema):
    # executescript commits an existing transaction: execute each DDL explicitly.
    for statement in legacy_schema.split(";"):
        if statement.strip():
            db.execute(statement)
    for table in LEGACY:
        db.execute(f"ALTER TABLE {table} ADD COLUMN analysis_version INTEGER NOT NULL DEFAULT 1 CHECK(analysis_version > 0)")
    for statement in TABLES:
        db.execute(statement)
    db.execute(f"PRAGMA user_version={VERSION}")
