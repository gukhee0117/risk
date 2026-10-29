import asyncio
import math
from dataclasses import replace
from datetime import datetime, timedelta

import httpx
import pytest

from app.domain import KST, RiskError
from app.main import create_app
from app.schemas.risk import display
from app.services.calendar import TradingCalendar
from app.services.calculation import calculate_grade
from conftest import make_prices


def request(api, method, path, **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(run())


@pytest.fixture
def api(market):
    _, now, sessions, repository, _, settings = market
    prices = make_prices(sessions)
    for ticker in ("005930", "000660"):
        repository.replace(calculate_grade(ticker, prices, sessions, now), prices)
    return create_app(settings, clock=lambda: now)


def test_grades_contract_and_single_stock_package(api):
    response = request(api, "GET", "/grades")
    assert response.status_code == 200
    grades = response.json()
    assert len(grades) == 2
    assert set(grades[0]) == {"ticker", "var975", "riskGrade", "volatility", "priceFrom", "priceTo", "asOf", "analysisVersion"}
    assert grades[0]["analysisVersion"] == 1
    assert grades[0]["asOf"].endswith("+09:00")
    assert "grade" not in grades[0]
    package = request(api, "POST", "/package/volatility", json={"items": [{"ticker": "005930", "weight": 1}]})
    assert package.status_code == 200
    assert package.json()["volatility"] == grades[0]["volatility"]


@pytest.mark.parametrize("body", [
    {}, {"items": []}, {"items": [{"ticker": 5930, "weight": 1}]},
    {"items": [{"ticker": "005930", "weight": 0}]},
    {"items": [{"ticker": "005930", "weight": -1}]},
    {"items": [{"ticker": "005930", "weight": True}]},
    {"items": [{"ticker": "005930", "weight": "1"}]},
    {"items": [{"ticker": "005930", "weight": 1, "customerId": 1}]},
    {"items": [{"ticker": "005930", "weight": 1}], "customerId": 1},
    {"items": [{"ticker": "005930", "weight": .5}]},
    {"items": [{"ticker": "005930", "weight": .5}] * 2},
    {"items": [{"ticker": "005930", "weight": 1}] * 78},
])
def test_bad_input_rejected_without_echo(api, body):
    response = request(api, "POST", "/package/volatility", json=body)
    assert response.status_code == 400
    assert "error" in response.json()
    assert "customerId" not in response.text


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_json_cannot_break_error_serialization(api, value):
    response = request(api, "POST", "/package/volatility",
                       content='{"items":[{"ticker":"005930","weight":' + value + '}]}',
                       headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_unknown_and_disabled_tickers(api):
    for ticker in ("999999", "385720"):
        response = request(api, "POST", "/package/volatility", json={"items": [{"ticker": ticker, "weight": 1}]})
        assert response.status_code == 422
        assert response.json()["error"]["tickers"] == [ticker]


def test_weight_tolerance_boundary_and_normalization(api):
    for second in (.499, .501):
        response = request(api, "POST", "/package/volatility", json={"items": [
            {"ticker": "005930", "weight": .5}, {"ticker": "000660", "weight": second},
        ]})
        assert response.status_code == 200
    response = request(api, "POST", "/package/volatility", json={"items": [
        {"ticker": "005930", "weight": .5}, {"ticker": "000660", "weight": .5011},
    ]})
    assert response.status_code == 400


def test_expiry_three_four_completed_sessions(market):
    calendar, now, sessions, repository, _, settings = market
    prices = make_prices(sessions)
    grade = calculate_grade("005930", prices, sessions, now)
    repository.replace(grade, prices)
    index = calendar.sessions.index(grade.price_to)
    for age in (3, 4):
        today = calendar.sessions[index + age]
        clock = datetime.combine(today, datetime.min.time(), KST).replace(hour=17)
        api = create_app(settings, clock=lambda: clock)
        rows = request(api, "GET", "/grades").json()
        assert bool(rows) == (age == 3)
        response = request(api, "POST", "/package/volatility", json={"items": [{"ticker": "005930", "weight": 1}]})
        assert response.status_code == (200 if age == 3 else 422)
    with repository.snapshot() as db:
        assert repository.grades(db)["005930"] == grade


def test_missing_calendar_and_db_fail_closed(market):
    _, now, _, _, _, settings = market
    for modified in (replace(settings, calendar=None), replace(settings, database=settings.database.parent / "absent.sqlite3")):
        response = request(create_app(modified, clock=lambda: now), "GET", "/grades")
        assert response.status_code == 503


def test_calendar_not_weekday_assumption_and_range(market):
    calendar, now, *_ = market
    assert calendar.latest(now) < now.date()  # today's 16:00 release has not happened
    with pytest.raises(RiskError):
        calendar.latest(now + timedelta(days=40))
    days = list(calendar.days)
    del days[20]
    with pytest.raises(RiskError):
        TradingCalendar(days)
    with pytest.raises(RiskError):
        TradingCalendar(calendar.days + [calendar.days[-1]])


def test_weekend_does_not_age_result(market):
    calendar, *_ = market
    friday = next(day for day in calendar.sessions if day.weekday() == 4)
    monday = next(day for day in calendar.sessions if day > friday)
    assert calendar.fresh(friday, monday)
    assert (monday - friday).days == 3


def test_provisional_half_up_policy():
    assert display(.12345) == .1235
    assert display(.12344) == .1234
