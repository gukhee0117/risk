# 검증

python -m pytest -q로 실행한다. 모든 날짜·가격은 합성이고 KIS는 MockTransport로 대체한다.
실제 키·공식 거래일 달력·외부 네트워크가 필요하지 않다.

- calculation: 독립 기대값, 모든 등급 경계, raw 판정, 양수 분위수 절대값, linear 보간, ddof=1, 99%, 연속 결측, 다일 수익률 금지, 공통 표본, 분산 효과.
- storage_batch: 원자적 롤백, 재실행, 실패 보존·성공 갱신, 동시 배치 잠금, 읽기 일관성, 백업, 비호환 스키마 거절.
- api_calendar: riskGrade 계약, 단일 종목과 패키지 일치, 비중/중복/비유한 입력, 만료 3/4거래일, 주말 제외, 달력 누락/범위 초과.
- kis: 수정주가 파라미터, 페이지 처리, 토큰 재사용, 중복/비정상 응답, 재시도 한도, 비밀정보가 없는 오류.
- cli: 설정 미비 시 네트워크 호출 전 실패, 성공 종료 코드와 비활성 종목 제외.
- version_recovery: 실제 구 스키마 보존 마이그레이션·백업·DDL 실패 롤백, 재시작 후 단조 증가, 동시 게시, 정정 기록과 원자적 체크포인트, 무변경 재분석, 실패/미완료만 재개, 실제 프로세스 강제 종료 후 잠금 재획득.
- calendar_operations: 개장일과 종료 시각 분리, 변경 종료+10분, 누락·범위 초과·출처 없는 시각 거절, NOT_DUE에서 호출 방지, 달력 버전 출력.
- package_alignment: 다른 기준일·결측·역순 요청의 공통 거래 구간, 독립 기대값 비교 및 STALE이어도 공통 표본 부족이면 실패하는 경계.

허용 오차 0.001·정규화·중복/0 비중 거절·최대 77개·HALF_UP은 아직 합의되지 않은 개발 기본안이다.
공식 달력 스냅샷의 정상일·변경일·미확인 미래 시간·해시 검증은 test_calendar_builder.py에 있다.
실제 KIS 로컬 배치 실측은 별도 선택 실행 도구로 수행했으며 결과는 ../LOCAL_BATCH_VALIDATION.md에 정리했다.
단위 테스트는 실제 키나 네트워크 없이 실행한다. 종가 관측 도구의 시작 시각·반복값/변경값 구분·
거래량 변화 분리·인증 오류 중단·로그 보존·키 없는 계획 출력은 test_close_observation.py에 있다.

실제 HTTP 검증 하네스는 tests/integration/live_risk_server.py다. 별도 maria 저장소에서
RISK_HTTP_PROJECT에 이 프로젝트 경로를 지정해 선택 실행하는 PythonRiskHttpIntegrationTest가 사용한다.
서버·가격·달력·SQLite는 모두 테스트 전용이며 KIS를 호출하지 않는다.

전체 구조 이관 검증은 tests/integration/verify_full_schema_migration.py를 사용한다.
이 스크립트는 소유가 확인된 임시 컨테이너만 허용하고, 검토된 이관 전 MARIA 커밋을 기본 기준으로 고정한다.
다른 기준은 --legacy-ref에 검토된 전체 커밋 SHA를 명시한다. 현재 HEAD를 기준으로 삼지 않으므로 연동 구현 커밋 이후에도 재현할 수 있다.

실제 KIS 실측은 tests/integration/verify_local_batch.py를 선택 실행한다. 실행 환경의 키를 읽고
새 전용 디렉터리의 SQLite에만 저장한다. 실제 인증·시세 요청이 발생하므로 일반 pytest 수집 대상이 아니다.
tests/integration/check_local_batch.py는 완료한 실측 DB의 독립 계산값·ASGI API·무호출 재개를 확인하고,
별도 백업 DB에 통신 실패를 주입해 기존 결과 보존과 무변경 복구를 검사한다. KIS를 호출하지 않는다.

운영 기능 회귀는 test_runtime.py·test_auth_health_wal_backup.py·test_config.py에 있다.
실제 APScheduler 기동·즉시 복구·단일 실행·KST·종료 후 재개·같은 기준일 시도 상한·품질 실패 반복 방지,
배치 대기 중 API 응답·수동 CLI 잠금 감지·성공 계산 시각과 게시 시각 분리·401/503·WAL 읽기와 백업,
같은 날짜 원자 교체·최근 7개 회전·백업 실패 시 이전 사본 보존과 백업만 재시도를 검증한다.

로컬 Linux 컨테이너 검증은 tests/integration/verify_risk_container.py다. 별도 named volume·루프백 포트를
사용하고 합성 가격만 게시한다. HTTP 인증·단일 종목 패키지·UID 10001·WAL·백업 CLI·다른 프로세스 잠금과
컨테이너 재시작/재생성 후 보존을 확인한다. KIS나 운영 DB에 접속하지 않으며 소유 라벨 확인 후 테스트 자원만 정리한다.

```powershell
docker build -t maria-risk:local-validation .
.\.venv\Scripts\python.exe -B -m tests.integration.verify_risk_container --image maria-risk:local-validation --report data/container-validation.json
```

MARIA가 호출하는 live_risk_server.py의 합성 HTTP 하네스 키는 synthetic-http-integration-key다.
인증 포함 실제 MARIA HTTP 통합 4개와 위험등급 테스트 총 32개는 임시 MariaDB에서 재검증했다. 과거 인증 없는 증적과 구분한다.

배포 전 점검은 test_preflight.py의 15개가 검증한다. 키 누락/개행·잘못된 환경·초기 DB 부재·만료·수능일
시간 미확인·손상된 DB/백업·구버전/비WAL 차단·쓰기 권한 실패를 확인한다. 원본 DB/백업 보존,
임시 파일 정리·배치 쓰기 중 커밋된 결과 조회·KIS 미호출도 포함한다. 전체 Python 회귀는 159개 통과했다.
컨테이너 하네스에서도 비루트 사용자로 실제 preflight --probe-storage를 실행하고 기존 등급·버전 보존을 확인한다.
