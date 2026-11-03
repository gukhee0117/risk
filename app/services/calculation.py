"""Pure historical calculations; policies are checked before estimation."""

import math

import numpy as np
import pandas as pd

from app.domain import Grade, Price, RiskError


def grade_for(value: float) -> int:
    if not math.isfinite(value) or value < 0:
        raise RiskError("INVALID_RESULT", "VaR가 유효하지 않습니다.")
    for grade, cap in ((6, .01), (5, .10), (4, .20), (3, .30), (2, .50)):
        if value <= cap:
            return grade
    return 1


def validate_samples(series: pd.Series, expected: int) -> None:
    valid = series.notna() & np.isfinite(series)
    longest = run = 0
    for ok in valid:
        run = 0 if ok else run + 1
        longest = max(longest, run)
    minimum = (99 * expected + 99) // 100
    if expected < 2 or int(valid.sum()) < max(2, minimum) or longest >= 5:
        raise RiskError("INSUFFICIENT_SAMPLES", "유효 표본 비율 또는 연속 공백 기준을 충족하지 않습니다.")


def returns_for(prices: list[Price], sessions: list) -> pd.Series:
    if len({p.day for p in prices}) != len(prices):
        raise RiskError("DUPLICATE_PRICE", "중복 가격 날짜가 있습니다.")
    by_day = {p.day: p for p in prices}
    values = []
    for previous, current in zip(sessions, sessions[1:]):
        left, right = by_day.get(previous), by_day.get(current)
        value = float(right.close / left.close - 1) if left and right and left.volume and right.volume else math.nan
        if not math.isfinite(value):
            value = math.nan
        values.append(value)
    return pd.Series(values, index=sessions[1:], dtype=float)


def calculate_grade(ticker, prices, sessions, as_of) -> Grade:
    by_day = {p.day: p for p in prices}
    latest = by_day.get(sessions[-1])
    if latest is None or not latest.volume:
        raise RiskError("LATEST_PRICE_MISSING", "기준일의 유효 종가가 없습니다.")
    series = returns_for(prices, sessions)
    validate_samples(series, len(sessions) - 1)
    values = series.dropna().to_numpy()
    var = abs(float(np.percentile(values, 2.5, method="linear"))) * math.sqrt(250)
    vol = float(np.std(values, ddof=1)) * math.sqrt(250)
    if not math.isfinite(vol):
        raise RiskError("INVALID_RESULT", "변동성이 유효하지 않습니다.")
    return Grade(ticker, var, grade_for(var), vol, sessions[1], sessions[-1], as_of)


def package_volatility(series_by_ticker: dict, weights: list[float]) -> float:
    frame = pd.DataFrame(series_by_ticker)
    common = frame.notna().all(axis=1)
    marker = pd.Series(np.where(common, 0., np.nan), index=frame.index)
    validate_samples(marker, len(frame))
    covariance = frame.loc[common].cov(ddof=1).to_numpy()
    w = np.asarray(weights, dtype=float)
    variance = float(w @ covariance @ w)
    if not math.isfinite(variance) or variance < -1e-12:
        raise RiskError("INVALID_RESULT", "공분산 계산 결과가 유효하지 않습니다.")
    return math.sqrt(max(0., variance)) * math.sqrt(250)
