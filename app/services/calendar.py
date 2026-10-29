"""Explicit exchange sessions and hours; provider finality is a separate concern."""

import csv
import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path

from app.domain import KST, RiskError, three_years_before


class TradingCalendar:
    def __init__(self, days, *, open_days=None, available=None, version=None):
        # Tuple timestamps mean regular session close, never KIS finality.
        if not days or days != sorted(days, key=lambda row: row[0]):
            raise RiskError("INVALID_CALENDAR", "달력 날짜 순서를 확인하세요.", 503)
        if any(b[0] - a[0] != timedelta(days=1) for a, b in zip(days, days[1:])):
            raise RiskError("INVALID_CALENDAR", "달력에 중복 또는 누락 날짜가 있습니다.", 503)
        self.days = days
        self.closes = dict(days)
        opened = set(open_days) if open_days is not None else {d for d, close in days if close is not None}
        if not opened <= self.closes.keys():
            raise RiskError("INVALID_CALENDAR", "개장일 범위를 확인하세요.", 503)
        self.sessions = [d for d, _ in days if d in opened]
        self.available = available or {}
        for day, close in days:
            if close is not None and (day not in opened or close.utcoffset() is None or close.astimezone(KST).date() != day):
                raise RiskError("INVALID_CALENDAR", "정규장 종료 시각을 확인하세요.", 503)
        for day, stamp in self.available.items():
            if day not in opened or stamp.utcoffset() is None or stamp.astimezone(KST).date() < day:
                raise RiskError("INVALID_CALENDAR", "제공 시각을 확인하세요.", 503)
            if self.closes[day] is not None and stamp < self.closes[day]:
                raise RiskError("INVALID_CALENDAR", "제공 시각이 정규장 종료보다 빠릅니다.", 503)
        canonical = [(d.isoformat(), d in opened, c.isoformat() if c else None) for d, c in days]
        self.version = version or hashlib.sha256(repr(canonical).encode()).hexdigest()

    @classmethod
    def from_csv(cls, path: Path, hours_path: Path | None = None):
        """Read official dates and separately sourced hours, without inferring hours.

        available_at is optional metadata, never a substitute for regular_close_at.
        A holiday-only file supports historical windows but cannot start a batch.
        """
        try:
            raw = path.read_bytes()
            with path.open(encoding="utf-8-sig", newline="") as stream:
                records = list(csv.DictReader(stream))
            hours, hours_raw = {}, b""
            if hours_path is not None:
                hours_raw = hours_path.read_bytes()
                with hours_path.open(encoding="utf-8-sig", newline="") as stream:
                    for row in csv.DictReader(stream):
                        day = date.fromisoformat(row["date"])
                        if day in hours or not row.get("source_ref", "").strip():
                            raise ValueError
                        hours[day] = row
            days, opened, available = [], set(), {}
            for row in records:
                day = date.fromisoformat(row["date"])
                if row["is_open"] not in ("0", "1"):
                    raise ValueError
                detail = hours.get(day, row)
                close_raw = detail.get("regular_close_at", "")
                ready_raw = detail.get("available_at", "")
                if row["is_open"] == "1":
                    opened.add(day)
                    if close_raw and not detail.get("source_ref", "").strip():
                        raise ValueError
                elif close_raw or ready_raw or day in hours:
                    raise ValueError
                days.append((day, datetime.fromisoformat(close_raw) if close_raw else None))
                if ready_raw:
                    available[day] = datetime.fromisoformat(ready_raw)
            if not hours.keys() <= opened:
                raise ValueError
            return cls(days, open_days=opened, available=available,
                       version=hashlib.sha256(raw + b"\x00" + hours_raw).hexdigest())
        except (OSError, UnicodeError, KeyError, ValueError, TypeError) as exc:
            raise RiskError("CALENDAR_UNAVAILABLE", "검증된 거래일·거래시간 파일을 확인하세요.", 503) from exc

    def _today(self, now):
        if now.utcoffset() is None:
            raise RiskError("INVALID_CLOCK", "시간대가 있는 업무시각이 필요합니다.", 503)
        today = now.astimezone(KST).date()
        if not self.days[0][0] <= today <= self.days[-1][0]:
            raise RiskError("CALENDAR_OUT_OF_RANGE", "달력에 현재 날짜가 없습니다.", 503)
        return today

    def latest(self, now: datetime) -> date:
        today = self._today(now)
        if today in self.sessions and self.closes[today] is None:
            raise RiskError("TRADING_HOURS_UNAVAILABLE", "오늘의 공식 종료 시각이 필요합니다.", 503)
        completed = [d for d in self.sessions if d < today or (d == today and self.closes[d] <= now)]
        if not completed:
            raise RiskError("CALENDAR_OUT_OF_RANGE", "완료된 거래일이 없습니다.", 503)
        return completed[-1]

    def batch_target(self, now, target=None):
        today = self._today(now)
        day = target if target is not None else today
        if not self.days[0][0] <= day <= self.days[-1][0] or day > today:
            raise RiskError("INVALID_BATCH_DATE", "배치 기준일 범위를 확인하세요.", 503)
        if day not in self.sessions:
            if target is not None:
                raise RiskError("INVALID_BATCH_DATE", "휴장일은 배치 기준일이 될 수 없습니다.", 503)
            return None
        close = self.closes[day]
        if close is None:
            raise RiskError("TRADING_HOURS_UNAVAILABLE", "기준일의 공식 종료 시각이 필요합니다.", 503)
        if now < close + timedelta(minutes=10):
            if target is not None:
                raise RiskError("BATCH_NOT_DUE", "정규장 종료 10분 후 실행할 수 있습니다.", 503)
            return None
        return day

    def window(self, end: date) -> list[date]:
        start = three_years_before(end)
        if end not in self.sessions or self.days[0][0] >= start or self.days[-1][0] < end:
            raise RiskError("CALENDAR_OUT_OF_RANGE", "3년 범위의 거래일 달력이 필요합니다.", 503)
        window = [d for d in self.sessions if start <= d <= end]
        predecessors = [d for d in self.sessions if d < start]
        if not predecessors or len(window) < 2:
            raise RiskError("CALENDAR_OUT_OF_RANGE", "직전 거래일이 필요합니다.", 503)
        return [predecessors[-1], *window]

    def state(self, price_to, current):
        if price_to not in self.sessions or price_to > current:
            return "EXPIRED"
        age = sum(price_to < d <= current for d in self.sessions)
        return "CURRENT" if age == 0 else "STALE" if age <= 3 else "EXPIRED"

    def fresh(self, price_to, current):
        return self.state(price_to, current) != "EXPIRED"
