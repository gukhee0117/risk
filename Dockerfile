FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    RISK_DB_PATH=/data/prices.db \
    RISK_CALENDAR_PATH=/app/config/krx/open-days.csv \
    RISK_TRADING_HOURS_PATH=/app/config/krx/trading-hours.csv \
    RISK_UNIVERSE_PATH=/app/config/universe.csv \
    RISK_SCHEDULER_ENABLED=false

WORKDIR /app
COPY requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt \
    && groupadd --gid 10001 risk \
    && useradd --uid 10001 --gid 10001 --no-create-home risk \
    && mkdir -p /data && chown risk:risk /data
COPY --chown=risk:risk app ./app
COPY --chown=risk:risk config ./config
USER risk
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).close()"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-graceful-shutdown", "120"]
