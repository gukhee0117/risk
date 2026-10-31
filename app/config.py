import os
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from app.domain import RiskError

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {"RISK_DB_PATH", "RISK_CALENDAR_PATH", "RISK_UNIVERSE_PATH",
           "RISK_TRADING_HOURS_PATH", "KIS_ENV_FILE", "KIS_ENV", "KIS_APP_KEY", "KIS_APP_SECRET",
           "RISK_SERVICE_KEY", "RISK_SCHEDULER_ENABLED", "RISK_SCHEDULER_POLL_SECONDS", "RISK_BATCH_MAX_ATTEMPTS"}
ALLOWED |= {"RISK_BACKUP_DIR", "RISK_BACKUP_KEEP"}


def read_env(path: Path, *, required=False, allowed=ALLOWED) -> dict[str, str]:
    """Read only approved literal values. Never execute or interpolate file contents."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError as exc:
        if not required:
            return {}
        raise RiskError("ENV_FILE_UNAVAILABLE", "지정한 환경설정 파일을 읽을 수 없습니다.", 503) from exc
    except (OSError, UnicodeError) as exc:
        raise RiskError("ENV_FILE_UNAVAILABLE", "지정한 환경설정 파일을 읽을 수 없습니다.", 503) from exc
    values = {}
    for raw in lines:
        line = raw.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key not in allowed:
            continue
        if value.startswith(("'", '"')):
            closing = value.rfind(value[0])
            if closing < 1 or (value[closing + 1:].strip() and not value[closing + 1:].lstrip().startswith("#")):
                raise RiskError("INVALID_ENV_FILE", "환경설정 값의 따옴표를 확인하세요.", 503)
            value = value[1:closing]
        else:
            value = re.sub(r"\s+#.*$", "", value).rstrip()
        values[key] = value
    return values


def configuration() -> dict[str, str]:
    explicit = os.environ.get("RISK_ENV_FILE")
    local = Path(explicit) if explicit else ROOT / ".env"
    if not local.is_absolute():
        local = ROOT / local
    values = read_env(local, required=explicit is not None)
    values.update({key: os.environ[key] for key in ALLOWED if key in os.environ})
    # Only load shared secrets when the environment/local file has not supplied them.
    shared = values.get("KIS_ENV_FILE")
    if shared and any(not values.get(key) for key in ("KIS_APP_KEY", "KIS_APP_SECRET")):
        path = Path(shared)
        if not path.is_absolute():
            path = local.parent / path
        credentials = read_env(path, required=True, allowed={"KIS_APP_KEY", "KIS_APP_SECRET"})
        values = {**credentials, **values}
    return values


@dataclass(frozen=True)
class Settings:
    database: Path = ROOT / "data/risk.sqlite3"
    calendar: Path | None = None
    universe: Path = ROOT / "config/universe.csv"
    weight_tolerance: Decimal = Decimal("0.001")
    kis_key: str | None = field(default=None, repr=False)
    kis_secret: str | None = field(default=None, repr=False)
    kis_environment: str = "demo"
    trading_hours: Path | None = None
    service_key: str | None = field(default=None, repr=False)
    scheduler_enabled: bool = False
    scheduler_poll_seconds: int = 60
    batch_max_attempts: int = 3
    backup_dir: Path | None = None
    backup_keep: int = 7

    @classmethod
    def from_env(cls):
        values = configuration()

        def path_for(key, default=None):
            raw = values.get(key)
            if not raw:
                return default
            path = Path(raw)
            return path if path.is_absolute() else ROOT / path

        def positive_int(key, default):
            try:
                value = int(values.get(key, str(default)))
                if value <= 0:
                    raise ValueError
                return value
            except ValueError as exc:
                raise RiskError("INVALID_CONFIGURATION", "실행 간격과 시도 횟수는 양의 정수여야 합니다.", 503) from exc

        enabled = values.get("RISK_SCHEDULER_ENABLED", "false").lower()
        if enabled not in {"true", "false", "1", "0"}:
            raise RiskError("INVALID_CONFIGURATION", "스케줄러 활성화 설정을 확인하세요.", 503)

        return cls(
            database=path_for("RISK_DB_PATH", ROOT / "data/risk.sqlite3"),
            calendar=path_for("RISK_CALENDAR_PATH"),
            universe=path_for("RISK_UNIVERSE_PATH", ROOT / "config/universe.csv"),
            kis_key=values.get("KIS_APP_KEY"),
            kis_secret=values.get("KIS_APP_SECRET"),
            kis_environment=values.get("KIS_ENV", "demo"),
            trading_hours=path_for("RISK_TRADING_HOURS_PATH"),
            service_key=values.get("RISK_SERVICE_KEY"),
            scheduler_enabled=enabled in {"true", "1"},
            scheduler_poll_seconds=positive_int("RISK_SCHEDULER_POLL_SECONDS", 60),
            batch_max_attempts=positive_int("RISK_BATCH_MAX_ATTEMPTS", 3),
            backup_dir=path_for("RISK_BACKUP_DIR"),
            backup_keep=positive_int("RISK_BACKUP_KEEP", 7),
        )
