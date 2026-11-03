"""Manual batch / maintenance entry point; explicit reanalysis stays available."""

import argparse
import json
from datetime import date
from pathlib import Path

from app.clients.kis import KisClient
from app.config import Settings
from app.domain import RiskError, now_kst
from app.repositories.sqlite import Repository
from app.services.batch import batch_lock, run_batch
from app.services.backup import daily_backup
from app.services.calendar import TradingCalendar
from app.universe import load_universe


def main():
    parser = argparse.ArgumentParser(description="risk batch / SQLite backup")
    commands = parser.add_subparsers(dest="command", required=True)
    batch = commands.add_parser("batch")
    batch.add_argument("--as-of", type=date.fromisoformat, help="고정할 기준 거래일 YYYY-MM-DD")
    batch.add_argument("--reanalyze", action="store_true", help="성공 종목도 재수집; 변경 결과만 새 버전 게시")
    commands.add_parser("migrate", help="기존 SQLite 백업 후 스키마 마이그레이션")
    commands.add_parser("calendar-status", help="공식 달력 기준일·버전·범위 확인 (시세 호출 없음)")
    preflight = commands.add_parser("preflight", help="환경·달력·SQLite·백업 점검 (KIS 호출·운영 DB 변경 없음)")
    preflight.add_argument("--probe-storage", action="store_true", help="임시 파일로 WAL·쓰기·삭제 권한 확인")
    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)
    args = parser.parse_args()
    client = None
    try:
        settings = Settings.from_env()
        repository = Repository(settings.database)
        if args.command == "preflight":
            from app.services.preflight import preflight as inspect_readiness
            result = inspect_readiness(settings, now_kst(), probe_storage=args.probe_storage)
            print(json.dumps(result, ensure_ascii=False))
            return 1 if result["status"] == "BLOCKED" else 0
        if args.command == "migrate":
            with batch_lock(repository.path.with_suffix(".batch.lock")):
                backup_path = repository.migrate()
            print(json.dumps({"status": "migrated", "backup": backup_path.name if backup_path else None}))
            return 0
        if args.command == "backup":
            if not repository.path.is_file():
                raise RiskError("STORAGE_UNAVAILABLE", "백업할 SQLite 파일이 없습니다.", 503)
            repository.backup(args.destination)
            print(json.dumps({"status": "backed_up"}))
            return 0
        if settings.calendar is None:
            raise RiskError("CALENDAR_UNAVAILABLE", "RISK_CALENDAR_PATH 설정이 필요합니다.", 503)
        calendar = TradingCalendar.from_csv(settings.calendar, settings.trading_hours)
        now = now_kst()
        if args.command == "calendar-status":
            print(json.dumps({"referenceDate": calendar.latest(now).isoformat(), "calendarVersion": calendar.version,
                              "coverageEnd": calendar.days[-1][0].isoformat()}))
            return 0
        if args.reanalyze and args.as_of is None:
            raise RiskError("INVALID_BATCH_DATE", "재분석은 --as-of 기준 거래일을 명시하세요.", 400)
        # Fail before constructing the KIS client if calendar/window is not ready.
        target = calendar.price_target(now, args.as_of)
        if target is None:
            print(json.dumps({"status": "NOT_DUE", "priceTo": None, "updated": [], "unchanged": [], "failed": [], "skipped": []}))
            return 0
        calendar.window(target)
        universe = load_universe(settings.universe)
        client = KisClient(settings.kis_key, settings.kis_secret, settings.kis_environment)
        result = run_batch(repository, calendar, universe, client, now, target_date=target, reanalyze=args.reanalyze)
        daily_backup(repository, target, settings.backup_dir, settings.backup_keep)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["failed"] else 0
    except RiskError as exc:
        print(json.dumps({"error": {"code": exc.code, "message": exc.message}}, ensure_ascii=False))
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
