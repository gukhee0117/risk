from datetime import date

import pytest

from app.domain import RiskError
from app.services.calculation import calculate_grade
from app.services.grading_policy import GradingPolicy, product_grade
from conftest import make_prices


@pytest.mark.parametrize('elevated,expected', [(False, 2), (True, 1)])
def test_stock_classification_keeps_market_metrics(market, elevated, expected):
    _, now, sessions, *_ = market
    prices = make_prices(sessions)
    original = calculate_grade('005930', prices, sessions, now)
    result = calculate_grade('005930', prices, sessions, now,
                             policy=GradingPolicy('STOCK', stock_elevated=elevated))
    assert result.risk_grade == expected
    assert (result.var975, result.volatility, result.price_from, result.price_to) == (
        original.var975, original.volatility, original.price_from, original.price_to)


@pytest.mark.parametrize('policy', [GradingPolicy('STOCK'), GradingPolicy('UNKNOWN'),
    GradingPolicy('ETF'), GradingPolicy('ETF', inception=date(2020, 1, 1)),
    GradingPolicy('FUND', inception=date(2025, 1, 1)),
    GradingPolicy('FUND', inception=date(2027, 1, 1), asset_grade=2)])
def test_missing_metadata_cannot_silently_become_standard_stock_or_fund(policy):
    with pytest.raises(RiskError) as error:
        product_grade(.1, policy, date(2026, 11, 5))
    assert error.value.code == 'GRADING_METADATA_REQUIRED'


def test_fund_three_year_boundary_and_asset_basis():
    today = date(2026, 11, 5)
    assert product_grade(.005, GradingPolicy('FUND', inception=date(2023, 11, 6), asset_grade=2), today) == 2
    assert product_grade(.005, GradingPolicy('FUND', inception=date(2023, 11, 5), asset_grade=2), today) == 6


def test_leveraged_etf_adjustment_and_floor():
    policy = GradingPolicy('ETF', inception=date(2020, 1, 1), leveraged_or_inverse=True)
    assert product_grade(.2, policy, date(2026, 11, 5)) == 3
    assert product_grade(.7, policy, date(2026, 11, 5)) == 1


def test_new_leveraged_etf_uses_reviewed_structure_grade_without_double_uplift():
    policy = GradingPolicy('ETF', inception=date(2026, 1, 1),
                           leveraged_or_inverse=True, asset_grade=1)
    assert product_grade(.01, policy, date(2026, 11, 5)) == 1


@pytest.mark.parametrize('asset_grade', [None, 2, 6])
@pytest.mark.parametrize('kind', ['ETF', 'FUND'])
def test_new_leveraged_fund_cannot_be_lowered_by_asset_input(asset_grade, kind):
    policy = GradingPolicy(kind, inception=date(2026, 1, 1),
                           leveraged_or_inverse=True, asset_grade=asset_grade)
    assert product_grade(.01, policy, date(2026, 11, 5)) == 1


@pytest.mark.parametrize('value,expected', [(.005, 2), (.3, 2), (.7, 1)])
def test_high_difficulty_fund_cannot_be_lower_risk_than_grade_two(value, expected):
    policy = GradingPolicy('FUND', inception=date(2020, 1, 1), high_difficulty=True)
    assert product_grade(value, policy, date(2026, 11, 5)) == expected


def test_new_high_difficulty_fund_applies_floor_after_asset_classification():
    policy = GradingPolicy('FUND', inception=date(2026, 1, 1), asset_grade=5, high_difficulty=True)
    assert product_grade(.005, policy, date(2026, 11, 5)) == 2
