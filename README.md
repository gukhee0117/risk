# risk

Python 3.12 / FastAPI / numpy / pandas / SQLite를 사용하는 과거 시세 기반 위험지표 서비스다.
Maria DB·고객 정보에는 접근하지 않으며 추천·AI 설명·성향 판단은 처리하지 않는다.

## 구현 범위

- GET /grades: 유효 종목의 VaR·riskGrade·변동성 배열.
- POST /package/volatility: 종목과 비중으로 공통 수익률 공분산 계산.
- KIS KRX 일봉 수정 종가 수집, 하루 1회 배치 CLI, SQLite 백업 CLI.
- 표본 99% 이상, 연속 5거래일 공백 시 신규 계산 불가, 이전 결과 3거래일 초과 시 만료.
- 종목별 가격·등급 원자적 교체, 실패 보존, 중복 배치 거절.

합성 데이터로 계산·API·저장을 검증했다. 공식 휴장일 대조와 실제 Python↔MARIA HTTP 통합 검증은 완료했으며, 현재 배치 코드의 실제 KIS 전체 실행과 NCP 운영 검증은 남아 있다.
실제 데이터로 실행하려면 아래 달력과 환경변수가 필요하다. 달력이나 초기 DB가 없으면 API는 503으로 응답한다.

[확인: 2026-10-29] data/krx-open-days.csv에 2023-10-27~2026-10-29의 개장일 목록을 생성했다.
KIS KOSPI·KOSDAQ 날짜와 기존 주식·ETF 검증 날짜를 대조했다. 별도로 받은 KRX 공식 달력은 아니다.
후속으로 사용자가 제공한 KRX 2023~2026년 XLS 휴장일과 전체 1,099일을 대조했고 차이가 없었다. 대조한 개장일 파일은 data/krx-official-open-days.csv이며, 원본 해시·검사 기록은 data/krx-official-calendar-verification.json에 있다.
현재 파서는 이 공식 개장일 목록을 읽고 별도 거래시간 파일과 결합할 수 있다. 다만 이 목록은 2026-10-29까지만 있으며, 공식 변경일을 포함한 거래시간 자료는 아직 연결하지 않았다. 운영에 앞서 기간 확장과 거래시간 검증이 필요하다.
정규장 종료+10분은 배치 시작 정책이며 KIS 최종 종가 확정 보장이 아니다. 서비스 검증 완료와 제공기관 최종 확정은 구분한다.

## 설치 및 실행 (PowerShell)

    .\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
    .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

OpenAPI: http://127.0.0.1:8000/docs. 서버 시작·문서 조회는 시세 API를 호출하지 않는다.
실행 의존성은 requirements.txt, 개발 의존성은 requirements-dev.txt, 검증 버전은 requirements-lock.txt다.
SQLite는 Python 표준 sqlite3, KIS 클라이언트는 HTTPX를 사용한다.

## 설정과 거래일 달력

| 환경변수 | 값 |
| --- | --- |
| RISK_CALENDAR_PATH | 검증된 실제 거래일 CSV 경로, 필수 |
| RISK_TRADING_HOURS_PATH | 별도 공식 거래시간 CSV 경로. 달력에 종료 시각을 함께 제공하면 생략 가능 |
| RISK_DB_PATH | 기본 data/risk.sqlite3 |
| RISK_UNIVERSE_PATH | 기본 config/universe.csv |
| KIS_APP_KEY, KIS_APP_SECRET | 배치에만 필요, 실행 환경에서 주입 |
| KIS_ENV | 기본 demo, 실제 투자 서버는 real |
| RISK_ENV_FILE | 기본 프로젝트 루트 .env, 다른 설정 파일을 사용할 때 지정 |
| KIS_ENV_FILE | 기존 키 파일을 참조할 때 지정, KIS 키 두 항목만 읽음 |

프로젝트 루트 .env를 읽으며 프로세스 환경변수가 우선한다. 로컬 .env에는 KIS_ENV_FILE=../maria/.env를 지정해 기존 키를 복사 없이 읽도록 연결했다.
지정한 공유 파일에서는 KIS_APP_KEY·KIS_APP_SECRET만 읽으며 그 밖의 Maria 설정을 가져오지 않는다.
키가 이미 실행 환경이나 risk .env에 모두 있으면 공유 파일을 읽지 않는다. 값은 실행·보간하지 않는 한 줄 리터럴 형식이다.
KIS_ENV_FILE 상대 경로는 risk .env 파일 위치 기준, RISK_*_PATH 상대 경로는 프로젝트 루트 기준이다.
배포에서는 기존 로컬 경로 대신 실행 환경에 키를 주입하거나 별도 .env를 사용한다. 형식은 .env.example을 참고한다.
키·토큰·SQLite는 Git에 저장하지 않는다. 기본 저장 경로 data/의 내용은 무시된다.

개장일 CSV는 date,is_open을 사용하며 휴장일도 포함해 날짜가 연속해야 한다. is_open은 0/1이다.
종료 시각은 별도 파일(date,regular_close_at,source_ref) 또는 개장일 파일의 같은 열로 제공한다.
regular_close_at은 오프셋을 포함한 해당 거래일의 정규장 종료 시각이며 source_ref에 공식 근거를 기록한다.
available_at은 선택적인 제공 시각 메타데이터이며 개장 여부·종료 시각·만료 판단을 대신하지 않는다.
평일 달력으로 대체하지 않는다. 현재 날짜 및 최대 3거래일 오래된 공통 3년 창의 직전 가격 날짜까지 포함해야 한다.
현재 날짜 누락이나 오늘 개장일의 종료 시각 누락은 503이다. 아직 사용하지 않을 미래 날짜 누락은 현재 처리를 막지 않지만 만료 전에 갱신해야 한다.

아래는 파일 형식만 설명하는 **가상 예시**다. 검증된 KRX 달력이 아니며 운영에 사용할 수 없다.

    date,is_open,regular_close_at,source_ref
    2026-10-24,0,,
    2026-10-25,0,,
    2026-10-26,1,2026-10-26T15:30:00+09:00,synthetic-example-only

## 하루 1회 배치와 백업

    $env:RISK_CALENDAR_PATH = 'C:\verified-data\krx-calendar.csv'
    .\.venv\Scripts\python.exe -m app.cli batch
    .\.venv\Scripts\python.exe -m app.cli batch --as-of 2026-10-28
    .\.venv\Scripts\python.exe -m app.cli batch --as-of 2026-10-28 --reanalyze
    .\.venv\Scripts\python.exe -m app.cli calendar-status
    .\.venv\Scripts\python.exe -m app.cli migrate
    .\.venv\Scripts\python.exe -m app.cli backup data\risk-backup.sqlite3

외부 스케줄러는 실제 NCP OS·컨테이너 환경 확인 후 정한다. 자동 실행은 종료+10분 이후이며 휴장일·시작 전 호출은 NOT_DUE로 종료한다.
재개 시 --as-of로 원래 기준일을 고정한다. 일반 재개는 성공 종목을 제외하고 실패·미완료 종목만 수집한다.
--reanalyze는 기준일 명시가 필수이며 성공 종목도 재수집한다. 동일 가격·거래량·계산 결과·기간이면 기존 버전과 asOf를 유지한다.
migrate는 API와 쓰기를 중단한 상태에서 실행한다. 구 스키마는 백업 후 트랜잭션으로 변환하고 기존 값은 유지한다. 운영 DB에는 아직 실행하지 않았다.
MARIA 동기화·공통 만료 조회는 별도 maria 저장소에 구현하고 로컬 HTTP로 검증했다. OS 작업 등록과 운영 활성화는 미완료다. 만료·동기화 계약은 [MARIA_SYNC_CONTRACT.md](MARIA_SYNC_CONTRACT.md)를 따른다.

배치는 프로세스 안에서 토큰을 재사용하며 요청 간격은 최소 1.3초다.
매번 전체 3년 수정 종가를 다시 받아 조정 시점이 다른 가격을 섞지 않는다.
성공 종목만 가격·등급을 함께 교체하며 실패 종목은 이전 상태를 보존한다.
출력은 runId/updated/unchanged/failed/skipped와 실패 코드다. 일부 실패 시 종료코드는 1, 전체 성공은 0이다.
잠금 파일은 남겨 두며 삭제하지 않는다. 잠금 자체는 프로세스 종료 시 OS가 해제한다. 로컬 Windows에서 실제 잠금 소유 프로세스를 강제 종료하여 검증했다. 서버의 공유 볼륨 잠금 특성은 별도 확인한다.
백업은 SQLite backup API로 새 파일에 생성한다. 기존 파일은 덮어쓰지 않는다.

## API 계약

GET /grades는 다음 필드의 배열이다. 원값으로 등급을 판정하고 표시값만 소수 4자리로 반올림한다.

    [{"ticker":"005930","var975":0.3125,"riskGrade":2,"volatility":0.241,"priceFrom":"2023-10-30","priceTo":"2026-10-28","asOf":"2026-10-29T09:00:00+09:00","analysisVersion":1}]

위 숫자·날짜는 형식 설명용이며 실측 결과가 아니다. 1등급이 가장 위험하고 6등급이 가장 안전하다.
3년 구간은 공통 최신 확정 시세일 기준이다. 종목마다 오래된 마지막 시세일을 기준일로 사용하지 않는다.

POST /package/volatility 요청:

    {"items":[{"ticker":"005930","weight":0.4},{"ticker":"000660","weight":0.6}]}

응답 예시:

    {"volatility":0.31,"asOf":"2026-10-29T09:00:00+09:00"}

비중은 소수 단위, ticker는 앞자리 0을 유지한 6자리 문자열이다.
패키지는 가장 오래된 유효 priceTo까지의 공통 3년 구간을 사용하며 공통 표본이 부족하면 계산 불가다.
만료된 종목은 GET 응답에서 제외하며 해당 종목이 포함된 패키지는 422로 응답한다.
이력은 지우지 않는다. Maria 사본도 priceTo 기준으로 같은 만료 정책을 적용해야 한다.

오류 예시:

    {"error":{"code":"GRADE_UNAVAILABLE","message":"유효한 등급이 없는 종목이 있습니다.","tickers":["005930"]}}

입력 오류는 400, 데이터·등급 부족은 422, 달력·설정·저장소 오류는 503이다.
오류에 요청 원문·키·토큰·공급자 원문을 포함하지 않는다.

**[제안/개발 기본값]** 양수 비중(1 이하), 최대 77개, 중복 거절, 합계 1±0.001 허용 후 정규화, HALF_UP 반올림을 구현했다.
이 입력·표시 상세는 아직 합의로 확정하지 않았다. app/schemas/risk.py와 app/services/risk.py에서 분리해 관리한다.

## 종목 목록과 남은 준비

config/universe.csv는 기존 77개 목록을 옮긴 파일이다. 부모 Maria DB·시드는 수정하지 않았다.
385720·448630은 코드 불일치, 091990은 오래된 종목으로 비활성화했다. 대체 종목을 임의로 넣지 않았다.
나머지 74개가 모두 계산 가능하다는 뜻은 아니다. 거래정지 등 품질 기준 미달 종목은 배치에서 실패한다.
명칭 차이 검토, 시드 수정·대체 코드 재검증, 공식 달력 준비, 실제 KIS 배치 확인이 운영 전 남은 작업이다.
서비스 간 인증과 실제 VM 배포는 이번 구현에 포함하지 않는다.

## 구조와 테스트

app/clients는 KIS, app/repositories는 SQLite, app/services는 계산·검증·배치,
app/schemas는 요청·응답, app/main.py는 API, app/cli.py는 배치·백업 진입점이다.
저장 구조·정책 상세: [IMPLEMENTATION.md](IMPLEMENTATION.md).

    .\.venv\Scripts\python.exe -m pytest -q

Windows 기본 임시 폴더에 접근할 수 없으면 프로젝트 안의 **새로운** 임시 경로를 지정한다.
--basetemp는 기존 경로를 정리하므로 실제 DB나 보관할 데이터 경로를 지정하면 안 된다.

    .\.venv\Scripts\python.exe -m pytest -q --basetemp=data\test-run-new -p no:cacheprovider

테스트는 합성 달력·가격과 HTTPX MockTransport를 사용한다. 외부 호출과 실제 키 없이 실행한다.
독립 기대값, 모든 등급 경계, 선형 분위수·표본 표준편차, 공분산 분산 효과, 99%·공백·만료 경계,
실패 보존·롤백·재실행·동시 읽기·잠금·백업·입력 오류·KIS 페이지/재시도를 검증한다.

공식 구현 근거: [NumPy percentile](https://numpy.org/doc/stable/reference/generated/numpy.percentile.html),
[pandas covariance](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.cov.html),
[KIS 기간별 시세 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_daily_itemchartprice/inquire_daily_itemchartprice.py).
