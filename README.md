# Python Risk 서비스

Python 3.12·FastAPI·numpy·pandas·SQLite로 종목 위험등급과 패키지 변동성을 계산한다.
시세 출처는 KIS다. MARIA DB·고객 정보에는 접근하지 않으며 추천·AI 설명·성향 판단은 처리하지 않는다.

운영 적용 전 [KIS 가격 기준 검증](KIS_PRICE_VERIFICATION.md)을 확인한다. 현재 demo의 최신 거래일 일봉 가격은 장후에도 변하며, 주말 재조회에서도 정규장 가격과 일치하지 않았다. 정규장 종가를 최초 수집·복구·재분석에서 일관되게 확보하는 방법은 아직 미확정이다. 자동 배치/Pull은 비활성으로 유지하고, 실제 서버 검증은 [인프라 확인 목록](INFRA_CHECKLIST.md)을 따른다.

## 실행과 인증

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

[.env.example](.env.example)에 있는 이름으로 실제 키·설정을 주입한다. 로컬 .env·실제 키·SQLite는 Git에 저장하지 않는다.
**두 업무 API에 RISK_SERVICE_KEY와 같은 X-Risk-Service-Key 헤더가 필요하다.**
키 설정 누락은 503 AUTH_CONFIGURATION, 잘못된/없는 요청 키는 401이다. MARIA GET /grades 클라이언트도 같은 키 헤더를 전송한다.

| 경로 | 동작 |
| --- | --- |
| GET /grades | CURRENT·STALE인 활성 종목의 최신 게시 결과 배열 |
| POST /package/volatility | 종목·비중의 공통 거래일 수익률 공분산으로 변동성 계산 |
| GET /health | 내부 헬스체크. 인증 없이 200. status·lastSuccessfulCalculationAt·lastBatchStatus·batch_running |

GET /grades 필드는 ticker·var975·riskGrade·volatility·priceFrom·priceTo·asOf·analysisVersion이다.
priceTo는 분석 기준 거래일이며 최초 게시 버전은 1이다. 같은 결과 재분석은 버전·asOf를 유지한다.
패키지 요청은 `{"items":[{"ticker":"005930","weight":1}]}` 형식이며 ticker는 앞자리 0을 보존한다.
계산·표시값 규칙은 [IMPLEMENTATION.md](IMPLEMENTATION.md), 연동 계약은 [MARIA_SYNC_CONTRACT.md](MARIA_SYNC_CONTRACT.md)를 따른다.

health의 성공 계산 시각은 UNCHANGED도 포함하며 게시 시각과 다르다. 일부 성공은 PARTIAL로 표시한다.
DEGRADED여도 프로세스가 살아 있을 수 있다. batch_running=null은 판단 불가다. health는 KIS를 호출하지 않는다.

## 배치·복구·저장

- RISK_SCHEDULER_ENABLED=true에서만 앱 내부 스케줄러가 실행된다. 컨테이너 1개·worker 1개다.
- Asia/Seoul 공식 달력 기준 종료+10분부터 실행한다. 정상일은 15:40이며 KIS 반영 보장 시각은 아니다.
- 기본 60초마다 실행 적격성을 확인한다. 시작 시 최신 실행 가능 거래일의 실패·미완료만 복구한다.
- 매번 종목별 전체 3년 수정주가를 조회하고 가격·등급·버전·정정·성공 상태를 원자적으로 저장한다.
- 기본 기준일별 종목 처리 총 3회. 표본·무결성 오류는 반복하지 않고 인증·설정·코드 오류는 자동 재개를 중단한다.
- 이전 성공 결과는 3거래일까지 허용하고 4거래일부터 차단한다. CURRENT 전용 제한은 없다.
- WAL·쓰기 연결 직렬화·별도 읽기 연결로 배치 중 API 조회를 지원한다. 스키마는 기존 v1을 유지한다.
- 배치 후 전체 DB를 일관되게 백업하고 최근 7개를 보관한다. 당일 결과 갱신 시 교체, 백업 실패는 백업만 재시도한다.

```powershell
.\.venv\Scripts\python.exe -m app.cli calendar-status
.\.venv\Scripts\python.exe -m app.cli preflight
.\.venv\Scripts\python.exe -m app.cli preflight --probe-storage
.\.venv\Scripts\python.exe -m app.cli batch
.\.venv\Scripts\python.exe -m app.cli batch --as-of 2026-10-29
.\.venv\Scripts\python.exe -m app.cli batch --as-of 2026-10-29 --reanalyze
.\.venv\Scripts\python.exe -m app.cli backup data/manual-backup-NEW.sqlite3
```

수동 batch는 기본 오늘만 대상이며 자동 시작 복구는 최신 실행 가능 거래일을 선택한다.
--reanalyze는 성공 종목도 재수집한다. 기존 v0 DB는 쓰기를 중지하고 migrate CLI로 보존 마이그레이션한다.
수동 backup은 새 파일만 허용한다. OS 배치 잠금 파일은 삭제하지 않으며 강제 종료 시 OS가 잠금을 해제한다.

## Docker·운영 준비

Dockerfile은 UID/GID 10001, /data 쓰기 권한, 포트 8000·worker 1개를 설정한다.
/data 디렉터리 전체를 영구 볼륨으로 연결한다. 기존 볼륨 권한은 별도 확인한다. NFS/CIFS에 WAL을 그대로 적용하지 않는다.
이미지에는 .env·키·실제 DB·문서를 넣지 않는다. 스케줄러는 이미지에서도 기본 비활성이다.
배포 보호는 health만으로 충분하지 않으므로 인프라에서 같은 배치 잠금과 연계해야 한다.
preflight는 환경변수 이름·달력·종목 목록·기존 DB의 스키마/WAL/무결성·유효 결과·백업을 JSON으로 점검한다.
--probe-storage에서만 고유 임시 디렉터리로 쓰기·WAL·삭제를 검사한다. 기존 DB 생성·수정·마이그레이션과 KIS 호출은 하지 않는다.
BLOCKED는 종료 코드 1, PASS/REVIEW_REQUIRED는 0이다. 경고와 manualChecks를 확인해야 하며 종료 코드 0이 배포 승인을 뜻하지 않는다.

- [운영 기준](OPERATIONS_READINESS.md): 설정 이름·NCP 전달·배포 잠금·백업·남은 검증의 현재 기준.
- [달력](config/krx/README.md): 개장일 2023~2026년. 일반 거래일은 공식 기본 종료 15:30을 적용하며, 미확인 특별일인 2026-11-19는 공지 확인 전까지 차단한다.
- [KIS 실측](LOCAL_BATCH_VALIDATION.md): 593회·13분 12초, 71개 게시·3개 표본 부족. 20시 이후 관측이다.
- [테스트](tests/README.md): 합성 데이터 회귀와 선택 실행 통합 검증.

현재 config/universe.csv는 기존 domestic_product 시드의 77개이며 74개가 활성 상태다. DB 변경이 자동 반영되지는 않는다.
정확한 시드가 확정되면 그 원본에 맞춘다. 기존 생성기의 77개 경로와 새 Catalog 6개 경로를 먼저 구분한다.

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q --basetemp data/test-run-NEW
```

--basetemp는 새로운 전용 경로만 지정한다. 테스트는 실제 KIS 키·네트워크 없이 계산 정확성·실패·복구·인증·WAL·백업을 검증한다.
운영 DB 변경·NCP 배포·자동 동기화 활성화는 수행하지 않았다.
