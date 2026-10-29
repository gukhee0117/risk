"""Shared values and controlled failures; no customer or Maria DB knowledge."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


class RiskError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, tickers=()):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.tickers = list(tickers)


@dataclass(frozen=True)
class Price:
    day: date
    close: Decimal
    volume: int

    def __post_init__(self):
        if not self.close.is_finite() or self.close <= 0 or self.volume < 0:
            raise RiskError("INVALID_PRICE", "가격 또는 거래량이 유효하지 않습니다.")


@dataclass(frozen=True)
class Grade:
    ticker: str
    var975: float
    risk_grade: int
    volatility: float
    price_from: date
    price_to: date
    as_of: datetime
    analysis_version: int = 1


def three_years_before(day: date) -> date:
    try:
        return day.replace(year=day.year - 3)
    except ValueError:
        return day.replace(year=day.year - 3, day=28)
