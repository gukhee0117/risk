# Python ↔ MARIA 위험등급 계약

2026-11-05 현재 계약이다. 가격 지표 조회와 주식 현재 등급 조회를 분리한다. 종목별 가격 게시 원자성과 3거래일 만료 정책은 유지한다.
Python은 자체 SQLite만 사용하며 MARIA DB·고객 정보에 접근하지 않는다.

## 인증과 경로

- GET /grades, POST /package/volatility, GET /stocks/{ticker}/classification은 **X-Risk-Service-Key** 헤더가 필요하다.
- Python 환경변수는 **RISK_SERVICE_KEY**다. MARIA에도 같은 값을 주입하고 헤더를 보내야 한다.
- Python 미설정은 503 AUTH_CONFIGURATION, 요청 키 누락·불일치는 401 UNAUTHORIZED다.
- 내부 GET /health는 인증 없이 200이며 상태·마지막 성공 계산 시각·배치 상태·batch_running을 제공한다.
- MARIA GET /grades 클라이언트도 RISK_SERVICE_KEY를 읽어 같은 헤더를 보낸다. 키 미설정·공백·개행은 요청 전에 차단한다.
- 인증 401·403은 기존 RISK_INVALID_RESPONSE로 기록하고 장애 재시도를 예약하지 않는다. 기존 주기적 Pull은 유지한다.
- 2026-10-30 초기 Pull/인증 검증에서 HTTP 통합 4개와 위험등급 테스트 20개를 임시 MariaDB로 실행했다. 최신 등급 분리 조회까지의 검증 수는 아래 현재 상태를 따른다.

## 현재 주식 등급 조회

`GET /stocks/{ticker}/classification`은 활성 주식에 대해 KIS의 현재 투자주의·경고·관리 상태를 조회해 1등급 또는 2등급을 반환한다. 응답은 `ticker`, `riskGrade`, `asOf`, `basis`이며 `basis`는 `STOCK_RULE_CURRENT_STATUS`다. 가격 DB·GET /grades 게시 결과·변동성 신선도에 의존하지 않는다.

KIS 상태는 매 요청에서 확인한다. 응답은 `Cache-Control: no-store`이며 Python은 직전 조회 실패를 정상 상태로 간주하지 않는다. MARIA 서비스는 짧은 60초 캐시를 사용하지만 캐시 만료 후 조회가 실패하면 만료된 값으로 대체하지 않는다. 이 경로는 SQLite의 가격 기반 등급 레코드를 갱신하지 않는다.

MARIA에서는 주식의 표시/선택 등급을 현재 분류 API로, ETF·펀드의 위험 지표와 등급은 기존 게시 지표로 조회한다. 변동성이 필요한 화면은 그 지표의 신선도를 별도로 확인한다. 따라서 주식 등급 조회 실패를 기존 가격 등급으로 숨기지 않으며, 주식 변동성이 만료돼도 현재 상태 등급의 판정과 구분한다.

2026-11-05 기준 MARIA 위험등급 연관 검증은 총 201개다. SQL/unit 검증과 실제 HTTP 통합은 별도 설정에서 실행했으며, 임시 MariaDB 및 합성 Python API를 사용했다. 자동 Pull 활성화나 운영 DB 변경은 하지 않았다.

## 데이터·버전

GET /grades는 최신 사용 가능한 게시 결과 배열이다. 과거 게시 버전 전체를 동기화하지 않는다.
종목 매핑 키는 앞자리 0을 보존한 ticker이며 domestic_product와 정확히 하나로 연결한다.

| Python 응답 | MARIA 저장 |
| --- | --- |
| var975 / riskGrade / volatility | var_975 / risk_grade / volatility |
| priceFrom / priceTo | price_from / price_to |
| analysisVersion | analysis_version, 최초 게시 1 |
| asOf | 같은 순간을 KST로 변환·초 단위 calculated_at 저장 |
| MARIA 수신 시각 | synced_at, 만료 나이를 바꾸지 않음 |

priceTo는 분석 기준 거래일이다. 기준일의 유효 가격이 없으면 기존 결과를 이전 priceTo로 보존한다.
동일 기간·가격·거래량·결과의 재분석은 버전·asOf를 유지한다. 계산 성공 시각은 health에서 별도로 확인한다.

MARIA는 트랜잭션·부모 행 잠금·조건부 SQL로 priceTo를 우선 비교하고, 같은 날짜의 높은 버전만 반영한다.
같은 날짜·버전·정규화 내용은 멱등 처리하고 내용이 다르면 충돌로 기록한다. 과거 날짜는 높은 버전도 거절한다.
이관 버전 0은 Python 검증 게시 수신 전 사용하지 않는다. 기존 데이터를 삭제하지 않는다.

## 달력·만료·장애

- 양쪽 모두 실제 Asia/Seoul 시장 시계와 같은 KRX 개장일·종료 시각 CSV 바이트·해시를 사용한다.
- 달력 버전은 SHA-256(개장일 파일 바이트 + 0x00 + 별도 시간 파일 바이트)다. 시간 파일이 없으면 마지막 바이트열은 비어 있다.
- 공식 달력 CSV는 UTF-8·LF로 전달한다. 현재 공유 버전은 e38f605c3bc3bd387370667211304b5e173446b9dbd6c95edfbdc92f6d44e358이다. 파일 두 개와 MARIA의 RISK_CALENDAR_VERSION을 함께 갱신하며 줄바꿈 변환을 하지 않는다.
- 2026년 말까지 일반 거래일은 기본 종료 15:30을 적용하고, 2026-11-19는 공식 시간 확인 전까지 비워 차단한다. 특별일 공지는 별도 반영한다. 수능일도 개장일이므로 거래일 나이 계산에서 제외하지 않는다.
- 기존 MARIA 업무용 SYSTEM_CLOCK은 유지한다. 시장 기준일은 정규장 종료, 배치 시작은 종료+10분이다.
- priceTo 이후 완료 거래일이 0이면 CURRENT, 1~3이면 STALE, 4 이상이면 EXPIRED다. CURRENT 전용 제한은 없다.
- 만료 결과는 보존하되 조회·추천·분석에 사용하지 않는다. 동기화 실패 중에도 MARIA가 직접 같은 만료를 판단한다.
- MARIA 소비자는 용도에 따라 현재 주식 분류와 사용 가능한 가격 지표를 분리해 조회한다. 기존 챗봇·절세 브리핑은 위험등급을 사용하지 않아 변경 대상에서 제외한다.
- GET 누락·빈 배열을 즉시 삭제·만료·정상 최신 데이터로 해석하지 않는다. 달력 누락도 평일로 추정하지 않는다.
- 동기화 재시도는 MARIA Pull만 수행한다. Python KIS 수집·계산을 다시 실행하지 않는다.
- DATETIME에 시간대가 없으므로 현재 MARIA 위험등급 트랜잭션은 JDBC 세션 +09:00을 요구한다. 공용 풀 변경은 기존 업무시계 영향 검토 후 진행한다.

MARIA 연동은 기존 상품 마스터와 승인된 `stock_risk_grade.analysis_version`, `risk_grade_sync_state`를 사용한다. 이 작업에서 운영 DB 테이블·컬럼을 추가하지 않았다. 실제 서비스 자동 Pull과 현재 등급 분리 조회의 로컬 통합 검증은 완료했으며 NCP 배포·운영 키 적용·상시 자동 동기화는 하지 않았다.
