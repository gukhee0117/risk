# Python Risk 서비스 인수인계

갱신: 2026-11-05 KST. 다른 채팅에서 작업을 재개하기 위한 현재 상태다.
대화 원문이 아니라 확정 사항·검증 결과·미완료 작업만 기록한다. 이후 변경은 이 문서를 갱신한다.

## 세션 간 메모리

### 2026-11-05 현재 상태 (이 요약이 아래 과거 기록보다 우선)

- 금융투자협회 참고3 원문 `docs/0002_[참고 3].hwp` 대조를 완료했다. 주식은 현재 투자주의·경고·관리 상태로 1/2등급, ETF·펀드는 설정 3년 기준으로 자산/구조 또는 VaR 기준, 고난도 상품은 3~6 산정 결과를 2등급으로 제한한다. 설정 3년 미만 레버리지·인버스 펀드는 1등급이다.
- 현재 주식 등급 API `GET /stocks/{ticker}/classification`과 MARIA 연동을 구현했다. KIS 상태 실패 시 과거 저장 등급으로 대체하지 않는다. 주식 VaR·변동성과 기존 패키지 변동성 API는 유지했다.
- 현재 risk 브랜치 `develop`은 `origin/develop`에 `e849cb3`까지 푸시돼 있다. 후속 문서 변경은 별도 커밋 및 PR 작업 중이다. `.idea/` 및 문서 외 사용자 변경을 커밋하지 않는다.
- risk Python 전체 239개 테스트 통과(리뷰 회귀 추가 후). MARIA 위험등급 연관 검증은 서로 다른 SQL/unit 및 HTTP 실행 설정에서 총 201개 통과. 전체 상품 격리 배치는 74개 중 71개 게시, 3개 표본 부족 미게시, SQLite integrity `ok`; 운영 DB에는 쓰지 않았다.
- 삼성전자 기존 가격 731개 기준 VaR 103.2765%는 저장값과 재계산이 일치했다. 가장 큰 하락 3일의 가격쌍 6개는 공개 시세와 일치했으나 전체 조정가격 이력이 검증된 것은 아니다. 상세 `KIS_PRICE_VERIFICATION.md`.
- 운영 스케줄러 `RISK_SCHEDULER_ENABLED=false`, MARIA 자동 Pull `RISK_SYNC_ENABLED=false`를 유지한다. NCP 배포·운영 DB 변경은 하지 않았다. 2026-11-19 KRX 특별 거래시간 확인 및 반영은 여전히 별도 남은 항목이다.
- 사용자 요청에 따라 공유 문서를 최신화해 커밋하고, 푸시 및 upstream PR을 생성한다. 커밋 작성자는 `gukhee`로 지정한다.

- 다음은 현재 코드/문서 변경 내역을 기준으로 이어간다. 이하 날짜별 항목은 당시 진행상황 기록이며 현재 상태로 해석하지 않는다.

#### 2026-11-02 이력

- 2026-11-02 17:17 KST 공공데이터 비교 완료: 사용자가 인증키 설정 후 V2 주식/ETF API의10/30 종가·거래량을 조회했고 오늘 KIS demo의4종목 모두 일치했다(225000/90900/90800/90640). 증적 `data/price-basis-public-20261102-check01.json`, 비교 `data/price-basis-comparison-20261102-check01.json`. 전 거래일 조회 방향의 표본 근거 확보. 배치 기준일 변경은 미적용이며 권리조정/다른 날짜/전환 시점 보장은 미확정. 서비스 DB/자동 실행 변경 없음. 인증키 값 기록 없음.

- 2026-11-02 16:56 KST KIS demo 재조회 완료: 10/30 수정종가005930=225000, 247540=90900, 069500=90800, 102110=90640. 주식2개는 주말값과 달라졌고 금요일15:40 관측값과 일치했다. ETF 첫 종목은 동일, 두 번째는 과거 기준 없음. 다음 거래일의 과거 일봉이 주말 최신일봉과 다를 수 있음을 실측했다. 공공데이터 대조/확정 시각 보장은 미완료. 증적 `data/price-basis-kis-20261102-1655.json`, 상세 `KIS_PRICE_VERIFICATION.md` 최상단. 서비스 DB/자동 실행 설정 변경 없음.

- 2026-11-01 후속 준비: `docs/risk-price-basis.md`의 전 거래일 비교 방향을 읽고 `tools/compare_price_basis.py`와 `docs/risk-price-basis-runbook.md`를 준비했다. 실행일 전 네트워크 조회를 막고 주식2·ETF2의 KIS/공공데이터 가격을 수동 비교한다. KIS 기존 기준은 금요일 최종값이 아닌 11/1 주말 증적임을 명시한다. 공공데이터 키는 Risk .env에 없고 활용 신청 상태는 미확인. Risk/MARIA의 KIS 앱키가 동일함을 값 출력 없이 확인했다. 서비스 코드·DB·자동 실행 설정 변경 및 실제 시세 조회·외부 문의 발송·추가 커밋/푸시는 하지 않았다. 실전 키 확보는 우선순위에서 보류하고 11/2 비교를 우선한다.

- 사용자 요청으로 Notion 메모리에 [MARIA Python Risk 서비스](https://app.notion.com/p/40bbed6380f0810eae4dd3d3a518d0d3)와 [국희의 작업 보고와 커밋 방식](https://app.notion.com/p/40bbed6380f0819c822af1b1f03c950f)을 저장하고 다시 읽어 확인했다. 키·비밀번호·토큰·덤프는 저장하지 않았다.
- 다음 세션은 상위 AGENTS.md와 이 파일, KIS_PRICE_VERIFICATION.md를 먼저 읽고 현재 Git/서비스 상태를 다시 확인한다. 최신 중단 지점은 실전용 키 설정 미발견 및 KIS 당일 정규장 가격 계약 미확정이다. 추가 코드 변경/커밋/푸시/상시 자동 실행은 수행하지 않았다.

## 2026-10-30 최종 로컬 검증 — 이전 상태보다 우선

- 후속 당일 정규장 종가 가능성 확인: 공식 포털의 공개 기본시세 목록/필드 설명을 직접 조회했고 시간외 API 2종도 3종목에서 실측했다. 시간외 일자별 stck_clpr는 주말 일봉과 동일, 시간외 기준가는0으로 대체 해법 미확정. 상세 KIS_PRICE_VERIFICATION.md 최상단. 로컬은 demo 설정이며 별도 실전용 키를 발견하지 못해 사용자에게 키 값이 아닌 설정 파일 경로/준비 여부를 질문했다. 답변에 따른 실전 대조 또는 공식 집계 기준 확인이 다음 단계다. 문의 질문 초안만 작성했고 외부 문의 발송/코드 변경/DB 변경/추가 커밋은 하지 않았다.

- 2026-11-01 01:08~01:09 KST 주말 재조회 12회 완료. 10/30 일봉 가격은 005930=220500, 247540=89800, 069500=90800. 앞의 두 종목은 금요일15:40 기록(225000/90900)과 다르며 원/수정주가 및 두 차례 반복값은 동일했다. 주말 재조회는 정규장 값 확보의 해법으로 검증되지 않았다. 다음 거래일 검증과는 구분한다. 증적 data/kis-weekend-recheck-20261101-010907.json, 상세 KIS_PRICE_VERIFICATION.md 최상단. 코드·서비스 DB·자동 실행 변경 없음.

- 17:43~17:46 추가 30회 API 검증 완료: 최근30일 일봉/현재가2는 당일 정규장 고정값 해법 아님. 15:30 지정 분봉은 주식 2종목에서 15:40 가격 재현, ETF는 0거래량 허봉/실제 마감 행 누락이라 일반화 불가. 과거 10/16 KODEX200은 원일봉88750/수정일봉88624/분봉88625로 수정·반올림 차이까지 검토 필요. 분봉은 최대1년으로 기존3년 일봉 대체 불가. 상세 KIS_PRICE_VERIFICATION.md 참조. 다음 거래일의 10/30 일봉 및 실전 환경 비교는 미완료, 예약 없음. 코드/DB 변경 없이 검증만 수행했다.

- 17:30 이후 KIS 추가 검증으로 중요한 데이터 기준 문제를 발견했다. `KIS_PRICE_VERIFICATION.md`를 우선 확인한다. 공식 한국투자증권 2026-09-08 공지에서 9/14부터 KRX 애프터마켓 16:00~20:00 운영 확인. demo의 당일 J 일봉 가격이 장후 현재가와 일치했고 계속 바뀌었다. 정규장 종가와 언제나 동일하다는 가정은 미검증이며, 애프터마켓 반영 가능성이 높다. 수정/원주가 12회 비교에서 차이 없음, 시장별 일봉/현재가 18회 대조 완료. 17:30 수정 일봉 가격은 005930=221500, 247540=90200, 069500=90800; 17:32 005930은 다시 222000. 업무 기준/API 필드 의미 확인 전 상시 자동 실행 준비 완료로 표현하지 않는다. 코드/DB/스케줄 변경 없음.

- 후속 사용자 지시: 실제 NCP 검증은 인프라 담당자가 수행하므로 접속을 진행하지 않고 전달용 목록을 작성했다. `INFRA_CHECKLIST.md`에 실제 서버 검증 항목·증적·회신 형식을 정리했다. 현재 로컬 MARIA와 Python 77개 종목 목록은 누락/추가/중복 없이 일치하지만 최종 시드 확정은 아니다.
- 추가 실측: 16:45 KST 세 종목 조회 성공. 16:30 대비 005930은 거래량만 변경, 247540은 종가·거래량 변경, 069500은 동일. 현재 총 18개 관측, 최종 확정 시각 미확인. 추가 예약은 없으며 기존 5회 요약 JSON은 당시 증적으로 보존한다.

- Python 회귀 159개 통과(기존 폐기 예정 경고 1개), MARIA 위험등급 32개 통과(실제 Python HTTP 통합 4개 포함). 전체 MARIA 회귀 결과가 아니다.
- Python 실제 APScheduler는 격리 DB·합성 시세로 자동 배치, 성공 종목 제외, 백업 복원 무결성 통과(KIS 호출 0회).
- MARIA 전체 앱 웹 실행 및 실제 서비스 Pull 검증: 71개 상품 단일 매핑, 최초 저장 71개, 두 번째 Pull 중복 71개·변경 0개·거절 0개, 사용 가능 등급 71개. 실제 Risk 스케줄러 자동 호출도 검증용 실행에서 통과했다. DB 성공 시각·다음 조회 시각 기록과 다음 poll 전 재호출 차단 확인. 검증용 앱 종료, 공유 .env의 자동 Pull false 유지.
- Linux 이미지 재검증: 비루트·인증/HTTP·WAL·백업·프로세스 간 잠금·재시작/재생성 보존 통과. DB/백업 디렉터리 임시 WAL/쓰기/삭제 probe도 통과.
- Docker MariaDB 재시작 후 등급·상품·업무시계 값과 KST 보존. 전체 백업을 별도 DB에 복원해 등급 수/버전 합계·상품 수·업무시계 값·동기화 상태 일치 확인. 이번에 만든 임시 컨테이너/볼륨은 소유 라벨 확인 후 정리했다.
- 전체 증적: data/final-local-validation-20261030.json. 복원 검증한 백업: data/maria-db-final-validated-backup-20261030.sql. SQL 덤프는 민감 데이터 가능·Git 제외·출력/커밋 금지.
- KIS demo 관측은 15:40·15:45·15:50·16:00·16:30 세 종목 총 15개 모두 성공. 005930 종가는 16:00 및 16:30 변경, 247540 종가는 16:30 변경, 069500 종가는 전체 동일(거래량은 16:00까지 변경). 16:30 최종 확정 보장 불가. 예약 마지막 결과 0, 다음 실행 없음. 추가 예약/배치 시각 변경은 하지 않았다.
- 남은 외부 항목: 실제 NCP 볼륨/배포 공유 잠금/서버 밖 백업·복원, 최종 시드, 11/19 공식 거래시간, KIS 최종 종가 시각 및 재확인 정책 결정. 운영 배포·상시 자동 실행 미실행. deploymentApproved=false.

## 먼저 읽을 파일과 위치

작업 폴더: `C:\Users\lkh_0\shinhanwork\maria_project\risk`

| 파일 | 용도 |
| --- | --- |
| `..\AGENTS.md` | 최상위 작업 규칙. 미확정 사항·DB 변경은 먼저 보고 |
| `HANDOFF.md` | 이 문서. 재개 지점과 현재 상태 |
| `OPERATIONS_READINESS.md` | 설정·배포·잠금·중단/재개·백업·달력의 현재 운영 기준 |
| `MARIA_SYNC_CONTRACT.md` | Python ↔ MARIA 인증·필드·버전·시간·만료 계약 |
| `IMPLEMENTATION.md` | 기존 계산·저장·복구 구조 |
| `config\krx\README.md`, `verification.json` | 달력 생성·미확인 특별일·공유 해시 |
| `..\maria\integration\risk\CONTRACT.md`, `OPERATIONS.md`, `VALIDATION.md` | MARIA 저장소의 팀 공유 문서 |
| `LOCAL_BATCH_VALIDATION.md` | 10월 29일 실제 KIS 실측의 과거 증적. 최신 테스트 수/달력 범위와 구분 |

## 사용자 원칙

- 한국어로 간결하게 보고하고 [결정]·[확인]·[제안]을 구분한다. 제안을 무조건 긍정하지 않고 문제를 설명한다.
- 계산식·업무 API 경로·종목별 원자적 저장·3거래일 만료 정책을 유지한다. 패키지 계산에 CURRENT 전용 제한을 추가하지 않는다.
- 새 테이블·컬럼은 최대한 추가하지 않는다. 꼭 필요하면 변경 전에 이유·영향을 보고한다.
- 운영 DB 변경, 실제 NCP 배포, 자동 동기화 활성화는 별도 검증 전 수행하지 않는다.
- `.env`·키·토큰·DB 연결 정보를 출력하거나 문서/커밋에 넣지 않는다.
- 팀 문서는 기존 내용을 갱신하고 폐기된 내용은 정리한다. 완료 작업을 남은 일에 계속 쌓지 않는다.
- 사용자 요청으로 Python 변경을 달력·운영 기능·배포 점검·KIS 검증·문서의 5개 단위로 커밋한다. 푸시는 요청받지 않았다. `.idea/`는 사용자 파일로 보존하고 커밋에서 제외한다. 비밀값·DB·덤프·data 아래 로컬 실행 도구/증적도 제외한다.

## 현재 구현·검증

- Python 3.12.10 / FastAPI / numpy / pandas / SQLite / APScheduler. API는 `/grades`, `/package/volatility`, `/health`다.
- 종목별 최근 3년 수정주가 전체 조회·페이지 수집·요청 단위 재시도·검증·계산·가격/등급 원자적 교체는 구현됐다. 다시 구현하거나 증분 수집으로 바꾸지 않는다.
- 단조 증가 분석 버전, 가격 정정 이력, 같은 결과의 버전 유지, 실패 보존, 성공 종목 제외 재개, 명시적 재분석을 지원한다.
- 앱 내부 KST 스케줄러·중복 잠금·재시작 복구·시도 상한·WAL·쓰기 연결 직렬화·API 읽기 분리·일관된 일일 백업/최근 7개 보관 구현 완료.
- 정상일 배치는 15:40, 변경일은 공식 정규장 종료+10분이다. 이 시각은 KIS 최종 종가 확정 보장이 아니다.
- 마지막 Python 전체 회귀: **159개 통과**, 신규 preflight 15개 포함. 기존 TestClient/httpx 폐기 예정 경고 1개는 테스트 실패가 아니다.
- 최종 로컬 Linux 이미지 `maria-risk:local-validation-20261030-preflight`에서 비루트 UID 10001·HTTP 인증·등급/패키지·WAL·백업·독립 프로세스 잠금·재시작/재생성 보존·preflight를 검증했다. 합성 데이터이며 KIS 호출 0회다.
- 결과 파일: `data/container-preflight-validation-20261030.json`. 검증용 임시 컨테이너·볼륨은 소유 라벨 확인 후 정리됐다.
- MARIA 위험등급 테스트 **32개 통과**(실제 Python HTTP+임시 MariaDB 통합 4개 포함). 전체 MARIA 1,436개 통과는 과거 기록이며 최신 전체 재실행 결과가 아니다.
- 이번 운영 기능에서 기존 Python SQLite v1 스키마 및 MARIA 테이블·컬럼을 추가 변경하지 않았다.

## 로컬 실행 상태와 중단 지점 — 중요

- `risk/.env`와 `maria/.env`에 같은 `RISK_SERVICE_KEY`를 설정했다. 값은 출력하지 않았다. 두 파일 모두 Git 제외다.
- Python은 `.env`를 읽고, MARIA는 `spring.config.import=optional:file:.env[.properties]` 및 `custom.risk.service-key`로 읽는다. 변경은 재시작 때 반영된다.
- 사용자가 서비스를 재시작했다. 마지막 실제 HTTP 확인에서 `127.0.0.1:8000/health`는 200/DEGRADED, `batch_running=false`였다.
- 키 없는 요청은 401, 올바른 키는 인증 통과 후 503 `STORAGE_UNAVAILABLE`였다. 기본 DB가 없기 때문이다. 현재 실행 여부는 새 채팅에서 다시 확인한다.
- 기본 서비스 DB `data/risk.sqlite3` 준비 완료. SQLite backup API로 원본을 보존하며 복제했고 스키마 v1·무결성·가격 51,901행·등급 71개 일치를 확인했다. WAL 설정 및 `data/backup/risk-2026-10-29.sqlite3` 백업 완료. 초기 배치/KIS 재수집은 실행하지 않았다. `RISK_SCHEDULER_ENABLED=false` 유지.
- 실제 Python HTTP: /grades 200·71개, 패키지 200, 무키 401, 표본 부족 종목 422 GRADE_UNAVAILABLE. health PARTIAL/DEGRADED·batch_running=false. 현재 priceTo=2026-10-29로 STALE 1거래일이다.
- 소스 DB는 **`data/local-batch-20261029/risk.sqlite3`**에 있다. 스키마 v1·가격 51,901행·등급 71개·모두 `priceTo=2026-10-29`, `analysisVersion=1`을 재확인했다.
- 소스는 실제 KIS demo 서버에서 10월 29일 20:17~20:30에 수집한 데이터다. 74개 조회/71개 게시, 207940·086520·012450은 표본 부족으로 미게시다.
- 서비스 초기화 시 소스 무결성·현재 종목/거래일 호환성·만료를 확인한다. 재사용한다면 SQLite backup API로 새 서비스 파일을 만들고 기존 파일은 덮어쓰지 않는다. WAL DB를 일반 파일 복사로 복제하지 않는다.
- 소스의 과거 달력 해시와 현재 배포 달력 해시는 다르다. 과거 증적/보고서 해시를 현재 값으로 덮어써 검증을 통과시키지 않는다. `check_local_batch.py`는 당시 달력과의 해시 일치를 요구한다.
- 71개 게시 결과가 유효해도 3개 표본 부족으로 health가 PARTIAL/DEGRADED일 수 있다. 전체 성공으로 꾸미거나 표본 정책을 완화하지 않는다.
- MARIA 로컬 설정 및 실제 Pull 검증 완료. Windows MariaDB 서비스는 중지·수동 시작, Docker maria-db는 127.0.0.1:3306->3306이다. 같은 데이터 볼륨 유지, 교체 전 중지된 옛 컨테이너는 볼륨 삭제 없이 정리했다. .env DB 비밀번호는 Docker root 설정과 일치하며 출력 금지. .env를 팀과 공유하므로 팀원 DB 비밀번호 일치는 별도 확인이 필요하다.
- MARIA .env Risk URL은 http://127.0.0.1:8000, 달력은 ../risk/config/krx/ 아래 상대 경로 및 현재 해시, RISK_SYNC_ENABLED=false다. maria 루트에서 실행하는 폴더 구조가 필요하다. 개인 절대 경로는 제거했다.
- Docker TZ=Asia/Seoul 및 mariadbd --default-time-zone=+09:00 설정. JDBC session/global +09:00, system KST, offsetSeconds=32400 확인. 사용자 명시 지시로 SYSTEM_CLOCK 기준값은 보정하지 않았고 계산 업무시각은 기존보다 +9시간 이동했다. 사용자는 업무시각은 나중에 조정한다고 했다.
- 기존 db/01_ria_ddl.sql에서 stock_risk_grade/risk_grade_sync_state 정의·외래키·초기 state_id=1만 추출해 로컬 DB에 적용했다. 전체 초기화 DDL 미실행. 이후 실제 Pull로 등급 71개 저장 및 sync state 성공 기록 완료.

## 15:40 실제 관측 — 계획한 5회 완료

- 사용자 요청으로 Windows 예약 MariaRisk-KIS-Observation-20261030을 등록했고 계획한 5회 모두 완료했다. CatchUp=false, 마지막 결과 0, 다음 실행 없음. 상세 결과는 최상단 최종 검증 항목 및 data/kis-observation-summary-20261030.json 참조.
- 인수인계 직전 시계 확인은 2026-10-30 **14:15 KST**였다. 새 채팅에서 실제 시각을 다시 읽는다.
- 도구: `tools/observe_kis_close.py`. 005930·247540·069500 세 종목을 15:40·15:45·15:50·16:00·16:30에 조회하는 계획이다.
- 이 도구는 관측만 하고 등급을 게시하지 않는다. 실제 조회 시각과 종가/거래량 해시 변화를 기록하며 같은 값 반복도 최종 확정 근거로 단정하지 않는다.
- 실측 전에는 키가 필요 없는 plan으로 확인한다. 시각을 바꾸거나 이미 지난 시각의 관측을 소급 생성하지 않는다.
- 검증 이후 자동 배치를 잠시 끌 의향이 있다. 현재 이미 비활성이다. 실제 중단/활성화는 그때 상태를 확인하고 사용자 지시에 따른다.

```powershell
.\.venv\Scripts\python.exe -B -m tools.observe_kis_close plan --as-of 2026-10-30
# 실제 해당 시각에만 snapshot 실행. 실제 KIS 요청 발생.
.\.venv\Scripts\python.exe -B -m tools.observe_kis_close snapshot --as-of 2026-10-30
```

## 달력과 시드

- 개장일 범위는 2023-01-01~2026-12-31, 975개 거래일이다. 일반일은 KRX 기본 종료 15:30을 적용하고 과거 확인된 변경일은 별도 시간이다.
- 974개 종료 시각이 있으며 **2026-11-19 수능일만 KRX 공식 시간 확인 전 차단**한다. 모든 11월 거래일을 막던 정책은 폐기했다.
- 수능일은 휴장일로 바꾸지 않아 만료 거래일 수에는 포함한다. 당일 시장 기준일·API·배치가 차단될 수 있고, 다음 일반일의 당일 배치는 15:40부터 가능하다.
- **11월 5일 전후** 수능일 공지를 확인할 계획이다. 최근 3년 14일 전 공지 사례에 따른 예상일이며 자동 알림은 설정하지 않았다.
- 공지 확인 후 `tools/build_krx_calendar.py`의 `SPECIAL_CLOSES`에 공식 출처/시간을 추가하고 CSV·verification.json·MARIA 예상 해시를 함께 갱신한다.
- CSV는 UTF-8·LF다. `.gitattributes`와 생성 도구에서 고정했다. 현재 공유 버전:

```text
e38f605c3bc3bd387370667211304b5e173446b9dbd6c95edfbdc92f6d44e358
```

- 위 버전은 MARIA `RISK_CALENDAR_VERSION`에 같은 두 CSV와 함께 배포해야 한다. 실제 MARIA/NCP 설정을 이미 갱신했다고 가정하지 않는다.
- Python은 `config/universe.csv`의 **77개 중 활성 74개**를 읽는다. MARIA `domestic_product`를 직접 조회하거나 자동 추종하지 않는다.
- 최종 시드가 나오면 목록·활성 상태와 MARIA 응답 검증의 최대 77개 제한을 함께 조정한다. 새 시드는 generator를 사용하지 않을 수도 있으므로 원본/생성 방식은 미확정이다.
- data-generator는 수정하지 않았다. 기존 `gradlew run`(77개)과 `gradlew generate`(Catalog 6개)가 공존하는 사실만 확인했다. 새 생성기 연동을 임의 구현하지 않는다.

## NCP·DB 계약과 남은 작업

- 위험등급 전용 시계는 실제 Asia/Seoul이며 기존 MARIA 업무용 SYSTEM_CLOCK과 분리한다. CURRENT 0거래일·STALE 1~3거래일·EXPIRED 4거래일 이상이다.
- MARIA는 GET /grades Pull, priceTo 우선/같은 날짜 analysisVersion 우선, 동일 응답 멱등, 동일 버전 내용 충돌 기록, 만료 사용 차단을 유지한다.
- 기존 승인된 MARIA 추가 구조는 `stock_risk_grade.analysis_version`과 `risk_grade_sync_state`다. 이관 버전 0은 미확인이며 동기화 상태는 state_id=1 한 행으로 초기화한다. 임의 더미 등급을 채울 필요는 없다.
- NCP ext4·로컬 SSD는 인프라 담당자가 확인했다. risk-data named volume을 /data에 연결하고 컨테이너 1개/worker 1개·UID/GID 10001을 사용한다.
- 실제 서버 볼륨 권한·영속성·배포 공유 flock·강제 종료 복구·서버 밖 주간 백업은 아직 검증하지 않았다. 학원 VM NFS 여부도 미확인이다.
- deploy.sh는 인프라 담당자 영역이다. health만 보고 배포를 허용하면 경쟁 조건이 있어 같은 배치 잠금을 사용해야 한다. 15:00 배치로 되돌리지 않는다.
- 남은 순서: 관측 결과에 따른 배치/재확인 정책 결정 → 수능 공지/최종 시드 준비 시 반영 → NCP 검증 → 별도 승인 후 운영 적용·상시 자동 실행. 로컬 데이터/API/Pull/실제 스케줄러/재시작/복원 검증은 완료했다.

## 자주 쓰는 명령과 Git 상태

```powershell
.\.venv\Scripts\python.exe -m app.cli preflight
.\.venv\Scripts\python.exe -m app.cli preflight --probe-storage
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
.\.venv\Scripts\python.exe -m pytest -q --basetemp=data/pytest-NEW-UNIQUE
```

- preflight는 KIS 호출·기존 DB 쓰기·자동 마이그레이션 없이 점검한다. probe 옵션만 고유 임시 디렉터리에 SQLite를 만들어 검사·정리한다. deploymentApproved는 항상 false다.
- 코드: `app/services/preflight.py`, `runtime.py`, `backup.py`, `calendar.py`, `app/cli.py`.
- 테스트: `tests/test_preflight.py`, `test_runtime.py`, `test_auth_health_wal_backup.py`, `test_calendar_builder.py`.
- PowerShell 기본 샌드박스에서 pytest 임시 폴더/검사 의존성 ACL 또는 Docker 파이프 접근이 거절되면 필요한 명령만 권한 승인을 요청한다. 기존 --basetemp를 재사용하지 않는다.
- Python 기능 커밋: b53d7f2(달력), 848628f(인증·스케줄러·WAL·백업), ea0ec7c(preflight·Docker), 270a222(KIS 검증 도구). 운영·연동·인수인계 문서는 별도 마지막 커밋으로 묶는다. `.idea/` 미추적 파일은 사용자 파일로 남긴다.
- MARIA 현재 develop HEAD는 `b2dd7847`이다. 최근 pull은 13개 파일의 업무시각 통일·감사로그·커뮤니티 DDL 변경이며 위험등급 코드/시드 목록은 그대로다. 미커밋은 integration/risk 문서 3개다.

## 새 채팅에 붙여 넣을 문구

```text
C:\Users\lkh_0\shinhanwork\maria_project\risk에서 작업합니다.
먼저 ..\AGENTS.md와 HANDOFF.md, OPERATIONS_READINESS.md를 읽고 이어서 진행해 주세요.
직전에는 서비스 DB 준비, MARIA 실제 Pull/스케줄러, 재시작/백업 복원 및 KIS 5회 관측을 완료했습니다. HANDOFF.md 최상단 최종 검증 항목을 우선 읽어 주세요. 자동 배치/Pull은 비활성입니다.
기존 계산식·API·원자성·3거래일 만료 정책을 유지하고, DB 구조 변경은 꼭 필요할 때 먼저 보고해 주세요.
운영 DB 변경·NCP 배포·자동 실행 활성화는 하지 말아 주세요. 키 값은 출력하지 마세요.
```
