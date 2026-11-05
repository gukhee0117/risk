from decimal import Decimal

from app.domain import RiskError
from app.schemas.risk import GradeResponse, PackageResponse, display
from app.services.calculation import package_volatility, returns_for


class RiskService:
    def __init__(self, repository, calendar, universe, now, tolerance=Decimal("0.001")):
        self.repository, self.calendar, self.universe = repository, calendar, universe
        self.now, self.tolerance = now, tolerance

    def _usable(self, grades, current):
        return {ticker: grade for ticker, grade in grades.items()
                if ticker in self.universe and self.universe[ticker].status == "active"
                and self.calendar.fresh(grade.price_to, current)}

    def grades(self):
        current = self.calendar.latest(self.now)
        with self.repository.snapshot() as db:
            grades = self._usable(self.repository.grades(db), current)
        return [GradeResponse(
            ticker=g.ticker, var975=display(g.var975), riskGrade=g.risk_grade,
            volatility=display(g.volatility), priceFrom=g.price_from, priceTo=g.price_to, asOf=g.as_of,
            analysisVersion=g.analysis_version,
        ) for g in grades.values()]

    def volatility(self, request):
        tickers = [item.ticker for item in request.items]
        if len(set(tickers)) != len(tickers):
            raise RiskError("DUPLICATE_TICKER", "중복 종목을 보낼 수 없습니다.", 400)
        total = sum((item.weight for item in request.items), Decimal(0))
        if abs(total - 1) > self.tolerance:
            raise RiskError("INVALID_WEIGHT_SUM", "비중 합계는 1이어야 합니다.", 400)
        weights = [float(item.weight / total) for item in request.items]
        current = self.calendar.latest(self.now)
        with self.repository.snapshot() as db:
            grades = self._usable(self.repository.grades(db), current)
            unavailable = [ticker for ticker in tickers if ticker not in grades]
            if unavailable:
                raise RiskError("GRADE_UNAVAILABLE", "유효한 가격 기반 위험지표가 없는 종목이 있습니다.", tickers=unavailable)
            end = min(grades[ticker].price_to for ticker in tickers)
            sessions = self.calendar.window(end)
            series = {ticker: returns_for(self.repository.prices(db, ticker), sessions) for ticker in tickers}
            volatility = package_volatility(series, weights)
        return PackageResponse(volatility=display(volatility), asOf=self.now)
