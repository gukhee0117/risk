import math
import statistics
from dataclasses import replace
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from app.domain import Price, RiskError, three_years_before
from app.services.calculation import calculate_grade, grade_for, package_volatility, returns_for, validate_samples
from conftest import make_prices


@pytest.mark.parametrize("cap,grade", [(.01, 6), (.1, 5), (.2, 4), (.3, 3), (.5, 2)])
def test_all_grade_boundaries(cap, grade):
    assert grade_for(cap) == grade
    assert grade_for(math.nextafter(cap, 0)) == grade
    assert grade_for(math.nextafter(cap, math.inf)) == grade - 1


@pytest.mark.parametrize("value", [-1, math.nan, math.inf])
def test_invalid_var(value):
    with pytest.raises(RiskError):
        grade_for(value)


def test_independent_var_and_sample_std(market):
    _, now, sessions, *_ = market
    prices = make_prices(sessions)
    result = calculate_grade("005930", prices, sessions, now)
    known = [-.02 if i % 2 == 0 else .01 for i in range(len(sessions) - 1)]
    assert result.var975 == pytest.approx(.02 * math.sqrt(250))
    assert result.volatility == pytest.approx(statistics.stdev(known) * math.sqrt(250))
    assert result.risk_grade == 2


def test_positive_quantile_uses_absolute_value(market):
    _, now, sessions, *_ = market
    result = calculate_grade("005930", make_prices(sessions, [.001] * (len(sessions) - 1)), sessions, now)
    assert result.var975 == pytest.approx(.001 * math.sqrt(250))
    assert result.volatility == pytest.approx(0, abs=1e-14)


def test_linear_quantile_independently_interpolated(market):
    _, now, sessions, *_ = market
    n = len(sessions) - 1
    changes = [-.04 + i * .0001 for i in range(n)]
    # Arithmetic progression: linear percentile lies exactly at position (n-1)*0.025.
    expected = abs(-.04 + (n - 1) * .025 * .0001) * math.sqrt(250)
    result = calculate_grade("005930", make_prices(sessions, changes), sessions, now)
    assert result.var975 == pytest.approx(expected)


def test_raw_grade_before_rounding(market):
    _, now, sessions, *_ = market
    daily = -.30004 / math.sqrt(250)
    result = calculate_grade("005930", make_prices(sessions, [daily] * (len(sessions) - 1)), sessions, now)
    assert result.var975 == pytest.approx(.30004)
    assert result.risk_grade == 2


def test_coverage_99_percent_round_up():
    series = pd.Series([0.] * 729)
    series.iloc[[0, 10, 20, 30, 40, 50, 60]] = np.nan
    validate_samples(series, 729)  # 722 / 729 passes
    series.iloc[70] = np.nan
    with pytest.raises(RiskError, match="유효 표본"):
        validate_samples(series, 729)


def test_four_five_consecutive_missing():
    series = pd.Series([0.] * 729)
    series.iloc[30:34] = np.nan
    validate_samples(series, 729)
    series.iloc[34] = np.nan
    with pytest.raises(RiskError):
        validate_samples(series, 729)


def test_no_multiday_return_or_zero_volume_fill(market):
    _, _, sessions, *_ = market
    prices = make_prices(sessions)
    zero = [replace(p, volume=0) if p.day == sessions[10] else p for p in prices]
    missing = [p for p in prices if p.day != sessions[10]]
    for values in (zero, missing):
        series = returns_for(values, sessions)
        assert pd.isna(series.loc[sessions[10]])
        assert pd.isna(series.loc[sessions[11]])
        assert pd.notna(series.loc[sessions[12]])


def test_latest_missing_and_long_halt_fail(market):
    _, now, sessions, *_ = market
    prices = make_prices(sessions)
    with pytest.raises(RiskError, match="기준일"):
        calculate_grade("005930", prices[:-1], sessions, now)
    halted = [replace(p, volume=0) if p.day in sessions[20:25] else p for p in prices]
    with pytest.raises(RiskError, match="유효 표본"):
        calculate_grade("005930", halted, sessions, now)


def test_portfolio_covariance_and_diversification():
    a = pd.Series([-.02, .01, .03, -.01] * 200)
    b = -a
    expected = statistics.stdev(a.tolist()) * math.sqrt(250)
    assert package_volatility({"a": a}, [1]) == pytest.approx(expected)
    assert package_volatility({"a": a, "same": a}, [.4, .6]) == pytest.approx(expected)
    assert package_volatility({"a": a, "b": b}, [.5, .5]) == pytest.approx(0, abs=1e-8)
    # Independently compute daily portfolio returns instead of covariance matrix formula.
    actual_daily = [.3 * x + .7 * y for x, y in zip(a, b)]
    assert package_volatility({"a": a, "b": b}, [.3, .7]) == pytest.approx(statistics.stdev(actual_daily) * math.sqrt(250))


def test_pairwise_covariance_does_not_hide_common_gaps():
    a, b = pd.Series([0.] * 729), pd.Series([0.] * 729)
    a.iloc[[0, 20, 40, 60]] = np.nan
    b.iloc[[10, 30, 50, 70]] = np.nan
    validate_samples(a, 729)
    validate_samples(b, 729)
    with pytest.raises(RiskError):
        package_volatility({"a": a, "b": b}, [.5, .5])


def test_invalid_prices_and_duplicate_dates(market):
    _, _, sessions, *_ = market
    for value in ("0", "-1", "NaN", "Infinity"):
        with pytest.raises(RiskError):
            Price(sessions[0], Decimal(value), 1)
    prices = make_prices(sessions)
    with pytest.raises(RiskError):
        returns_for(prices + [prices[0]], sessions)


def test_leap_year_cutoff():
    from datetime import date
    assert three_years_before(date(2024, 2, 29)) == date(2021, 2, 28)
