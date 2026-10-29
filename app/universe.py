import csv
import re
from dataclasses import dataclass
from pathlib import Path

from app.domain import RiskError


@dataclass(frozen=True)
class Instrument:
    ticker: str
    name: str
    status: str


def load_universe(path: Path) -> dict[str, Instrument]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            entries = [Instrument(row["ticker"], row["name"], row["status"]) for row in csv.DictReader(stream)]
        if not entries or len({e.ticker for e in entries}) != len(entries):
            raise ValueError
        if any(not re.fullmatch(r"[0-9]{6}", e.ticker) or e.status not in ("active", "code_mismatch", "stale") for e in entries):
            raise ValueError
        return {e.ticker: e for e in entries}
    except (OSError, KeyError, ValueError) as exc:
        raise RiskError("UNIVERSE_UNAVAILABLE", "종목 목록 파일을 확인하세요.", 503) from exc
