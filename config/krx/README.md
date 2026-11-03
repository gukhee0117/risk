# KRX 달력 스냅샷

- `open-days.csv`: 2023-01-01~2026-12-31, 1,461일·975개 거래일.
- `trading-hours.csv`: 2026년 말까지 974개 개장일의 정규장 종료 시각. 미확인 수능일 2026-11-19는 비워 둔다.
- `verification.json`: 원본 XLS·생성 CSV 해시, 범위와 제한 사항.

개장일은 사용자가 KRX 공식 휴장일 화면에서 내려받아 제공한
2023~2026년 XLS의 휴장일과 주말을 대조해 생성했다. 원본은 수정하지 않았다.
정규장 종료는 [KRX 거래제도](https://regulation.krx.co.kr/contents/RGL/03/03010100/RGL03010100T1.jsp)의
15:30을 적용했다. 2023-11-16·2024-11-14·2025-11-13은 증권사가 재게시한 KRX 공지의
16:30을 적용했다. 해당 날짜 행의 `source_ref`에서 확인할 수 있다.

**일반 개장일은 공식 기본 규정의 15:30 종료·15:40 배치를 적용한다.**
과거 확정된 특별일은 확인된 종료 시각을 유지하고, **2026-11-19는 KRX 시간 공지 확인 전까지 차단**한다.
수능일은 휴장일로 바꾸지 않아 3거래일 만료 계산에는 포함한다. 2027년 개장일은 추정하지 않는다.

2026-10-30 직접 조회에서는 KRX 휴장일 화면·보도자료 화면과 정규장 09:00~15:30 규정 원문을 확보했다.
다만 게시판의 실제 조회 데이터는 빈 응답이었고 2026년 수능일의 공식 변경 공지 원문은 확보하지 못했다.
일반일에 기본 규정을 적용하는 것은 서비스 운영 정책이며 미래 변경 공지가 없다는 보장은 아니다.
새 공지는 계속 확인하고 반영한다. 현재 미확인 종료 시각은 2026-11-19 한 개다.

**2026-11-03 기준 공지는 아직 확인되지 않았다.** 11월 5일 전후는 최근 게시 사례에 따른 예상 시점일 뿐 올해 게시일을 보장하지 않는다. 공식 공지를 계속 확인한다.
확인 후 `SPECIAL_CLOSES`에 날짜·종료 시각·KRX 출처를 추가하고 CSV·검증 보고서·MARIA 예상 해시를 함께 갱신한다.
`SPECIAL_CLOSES`에 확인된 시각이 있으면 `UNCONFIRMED_CLOSES`보다 우선한다. 미확인 특별일은 기준일 조회도 차단한다.
다음 일반일의 당일 배치는 정상 시간에 가능하지만, 미확인 특별일을 대상으로 아침 복구를 수행하지는 않는다.

종료+10분은 배치 시작 시각이다. `available_at`은 비워 두었으며 KIS의 최종 종가 확정을
보장하는 값으로 사용하지 않는다. 휴장일 목록의 서명·원본 배포처를 독립 인증한 것은 아니다.

생성 도구는 `tools/build_krx_calendar.py`다. XLS 읽기에만 선택적인 `xlrd`가 필요하며
애플리케이션 실행 의존성에는 추가하지 않았다. 분석 환경에 설치된 도구로 다음을 실행한다.

```powershell
.\.venv\Scripts\python.exe -m pip install --target data/inspection-deps -r tools/requirements-calendar.txt
$env:PYTHONPATH = (Join-Path (Get-Location) 'data/inspection-deps')
.\.venv\Scripts\python.exe -B -m tools.build_krx_calendar `
  --source-dir docs --output-dir config/krx --hours-through 2026-12-31
```

`--hours-through`는 기본 규정의 적용 범위이며 모든 날짜의 개별 공지가 확인됐다는 뜻은 아니다.
이전 `--hours-reviewed-through`도 별칭으로 지원한다. `UNCONFIRMED_CLOSES`의 미확인 날짜에는
범위 안이어도 기본 시각을 채우지 않는다. 설정 두 항목은 다음과 같다.

```dotenv
RISK_CALENDAR_PATH=config/krx/open-days.csv
RISK_TRADING_HOURS_PATH=config/krx/trading-hours.csv
```

MARIA와 공유할 때는 CSV 두 파일을 함께 전달하고 동일한 바이트·해시를 사용한다.
이 스냅샷의 공유가 MARIA 운영 설정 변경이나 자동 동기화 활성화를 뜻하지는 않는다.

## 파일 형식과 공유 버전

CSV는 UTF-8·LF로 생성하고 `.gitattributes`에서 해당 경로의 LF를 고정한다.
기존 933개 시각·전체 개장일은 유지했고 일반일 41개 시각을 추가해 공유 버전이 변경됐다.
해시 산식은 기존과 같은 SHA-256(개장일 파일 + 0x00 + 거래시간 파일)이다.

현재 `calendarVersion` 및 MARIA의 `RISK_CALENDAR_VERSION`에 적용할 값:

```text
e38f605c3bc3bd387370667211304b5e173446b9dbd6c95edfbdc92f6d44e358
```

배포 시 CSV 두 파일과 예상 해시를 함께 교체한다. 운영 환경변수는 이번 작업에서 변경하지 않았다.
원본 XLS 재생성·LF 공유 형식을 유지했고 Python 144개 회귀 테스트를 확인했다.
일반일·미확인 수능일·다음 일반일과, 합성 공지 반영 후 종료+10분 실행을 검증했다.
