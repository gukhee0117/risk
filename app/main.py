"""Application factory; importing the module never calls KIS or opens a DB."""

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import Settings
from app.domain import RiskError, now_kst
from app.repositories.sqlite import Repository
from app.schemas.risk import GradeResponse, PackageRequest, PackageResponse
from app.services.calendar import TradingCalendar
from app.services.risk import RiskService
from app.universe import load_universe


def create_app(settings: Settings | None = None, clock=now_kst):
    settings = settings or Settings.from_env()
    api = FastAPI(title="Risk Service", version="0.2.0")

    @api.exception_handler(RiskError)
    async def controlled_error(_, exc: RiskError):
        return JSONResponse(status_code=exc.status, content={"error": {
            "code": exc.code, "message": exc.message, "tickers": exc.tickers,
        }})

    @api.exception_handler(RequestValidationError)
    async def invalid_request(_, exc):
        # Pydantic errors may contain customer-supplied non-finite values: never echo input.
        return JSONResponse(status_code=400, content={"error": {
            "code": "INVALID_REQUEST", "message": "요청 필드와 비중을 확인하세요.", "tickers": [],
        }})

    def service():
        if settings.calendar is None:
            raise RiskError("CALENDAR_UNAVAILABLE", "RISK_CALENDAR_PATH 설정이 필요합니다.", 503)
        if not settings.database.is_file():
            raise RiskError("STORAGE_UNAVAILABLE", "배치로 초기 데이터를 준비하세요.", 503)
        calendar = TradingCalendar.from_csv(settings.calendar, settings.trading_hours)
        return RiskService(Repository(settings.database), calendar, load_universe(settings.universe), clock(), settings.weight_tolerance)

    @api.get("/grades", response_model=list[GradeResponse])
    def grades(risk: RiskService = Depends(service)):
        return risk.grades()

    @api.post("/package/volatility", response_model=PackageResponse)
    def volatility(request: PackageRequest, risk: RiskService = Depends(service)):
        return risk.volatility(request)

    return api


app = create_app()
