"""Application factory; importing the module never calls KIS or opens a DB."""

import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import Settings
from app.clients.kis import KisClient
from app.domain import RiskError, now_kst
from app.repositories.sqlite import Repository
from app.schemas.risk import GradeResponse, PackageRequest, PackageResponse, StockClassificationResponse, Ticker
from app.services.calendar import TradingCalendar
from app.services.risk import RiskService
from app.services.runtime import BatchRuntime, start_scheduler
from app.universe import load_universe

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, clock=now_kst, *, runtime=None, scheduler_factory=start_scheduler,
               classification_client_factory=KisClient):
    settings = settings or Settings.from_env()
    runtime = runtime or BatchRuntime(settings, clock=clock)

    @asynccontextmanager
    async def lifespan(_):
        scheduler = None
        if settings.scheduler_enabled:
            scheduler = scheduler_factory(runtime)
        try:
            yield
        finally:
            runtime.stop()
            if scheduler is not None:
                # Let the current stock finish atomically, then leave others pending.
                import asyncio
                await asyncio.to_thread(scheduler.shutdown, wait=True)

    api = FastAPI(title="Risk Service", version="0.3.0", lifespan=lifespan)
    api.state.batch_runtime = runtime

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

    def authorize(key: str | None = Header(default=None, alias="X-Risk-Service-Key")):
        if not settings.service_key:
            raise RiskError("AUTH_CONFIGURATION", "서비스 인증 설정이 필요합니다.", 503)
        if key is None or not hmac.compare_digest(key.encode('utf-8'), settings.service_key.encode('utf-8')):
            raise RiskError("UNAUTHORIZED", "서비스 인증이 필요합니다.", 401)

    @api.get('/health')
    def health():
        status = {"status": "DEGRADED", "lastSuccessfulCalculationAt": None, "lastBatchStatus": None,
                  "batch_running": None}
        try:
            status['batch_running'] = runtime.batch_running()
            if settings.database.is_file():
                status.update(Repository(settings.database).health())
            if status['lastBatchStatus'] == 'RUNNING' and status['batch_running'] is False:
                # Persisted checkpoint after a killed process, not a live batch.
                status['lastBatchStatus'] = 'INTERRUPTED'
            calendar = TradingCalendar.from_csv(settings.calendar, settings.trading_hours) if settings.calendar else None
            current = calendar.latest(clock()) if calendar else None
            with Repository(settings.database).snapshot() as db:
                grades = Repository.grades(db)
            usable = any(calendar.fresh(g.price_to, current) for g in grades.values()) if current else False
            if (usable and settings.service_key and not runtime.last_error
                    and status['lastBatchStatus'] not in {'FAILED', 'PARTIAL', 'INTERRUPTED'}):
                status['status'] = 'OK'
        except RiskError as exc:
            logger.warning("Health details unavailable: %s", exc.code)
            pass
        # Liveness remains 200 even when calculations fail; do not cause restart loops.
        return status

    @api.get("/grades", response_model=list[GradeResponse], dependencies=[Depends(authorize)])
    def grades(risk: RiskService = Depends(service)):
        return risk.grades()

    @api.get('/stocks/{ticker}/classification', response_model=StockClassificationResponse,
             dependencies=[Depends(authorize)],
             description='Current stock rule classification using a live KIS status check. '
                         'Independent of price-history freshness; contains no VaR or volatility. '
                         'Provider failure returns an error, never a cached normal classification.')
    def stock_classification(ticker: Ticker, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        instrument = load_universe(settings.universe).get(ticker)
        if instrument is None or instrument.status != 'active' or instrument.kind != 'STOCK':
            raise RiskError('STOCK_CLASSIFICATION_UNAVAILABLE', 'Active domestic stock is required.', 422, [ticker])
        client = classification_client_factory(settings.kis_key, settings.kis_secret, settings.kis_environment)
        try:
            elevated = client.stock_elevated(ticker)
            if type(elevated) is not bool:
                raise RiskError('GRADING_METADATA_REQUIRED', 'Current stock status is required.', 503, [ticker])
            return StockClassificationResponse(ticker=ticker, riskGrade=1 if elevated else 2, asOf=clock())
        finally:
            client.close()

    @api.post("/package/volatility", response_model=PackageResponse, dependencies=[Depends(authorize)])
    def volatility(request: PackageRequest, risk: RiskService = Depends(service)):
        return risk.volatility(request)

    return api


app = create_app()
