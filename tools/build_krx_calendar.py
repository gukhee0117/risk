"""Build application CSVs from unchanged, user-provided KRX holiday exports.

xlrd is an inspection-only dependency; it is not an application dependency.
Apply official regular hours within explicit coverage; unresolved special
sessions remain unknown (fail closed), without blocking every future date.
"""

import argparse
import csv
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

HOLIDAY_SOURCE = "https://open.krx.co.kr/contents/MKD/01/0110/01100305/MKD01100305.jsp"
REGULAR_SOURCE = "https://regulation.krx.co.kr/contents/RGL/03/03010100/RGL03010100T1.jsp"
SPECIAL_CLOSES = {
    date(2023, 11, 16): ("16:30", "https://www.hanwhawm.com/main/bbs/indexView.cmd?cc_gubun=1&mode=center&nn_id=65612&num=2252&pageVal=1&vc_bid=notice"),
    date(2024, 11, 14): ("16:30", "https://www.next-securities.com/board/boardDetail.do?bbsCategory=NT01&bbsId=BBSMSTR_000000000240&content=4000198&nttId=11594"),
    date(2025, 11, 13): ("16:30", "https://www.imfnsec.com/upload/C_A001/2025/11/%5B10110039%5D20251113.pdf"),
}
# The Ministry confirms the exam date, not the exchange's session hours.
# Add an official KRX entry to SPECIAL_CLOSES only after its notice is checked.
UNCONFIRMED_CLOSES = {
    date(2026, 11, 19): "https://www.moe.go.kr/boardCnts/viewRenew.do?boardID=294&boardSeq=100526&lev=0&m=020402",
}


def read_holidays(source_dir, years):
    import xlrd

    holidays, sources = {}, []
    weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    for year in years:
        path = source_dir / f"{year}.xls"
        book = xlrd.open_workbook(path)
        if book.nsheets != 1:
            raise ValueError("Expected one KRX holiday sheet")
        sheet = book.sheet_by_index(0)
        if sheet.ncols != 3:
            raise ValueError("Expected date, weekday, reason columns")
        annual = {}
        for raw, weekday, reason in (sheet.row_values(n) for n in range(1, sheet.nrows)):
            day = date.fromisoformat(raw.strip())
            if day.year != year or day in annual or weekday != weekdays[day.weekday()] or not reason.strip():
                raise ValueError("KRX holiday year/duplicate/weekday/reason mismatch")
            annual[day] = reason.strip()
        if not annual or list(annual) != sorted(annual):
            raise ValueError("Missing or unsorted KRX holidays")
        holidays.update(annual)
        sources.append({"file": path.name, "holiday_rows": len(annual),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return holidays, sources


def calendar_rows(start, end, holidays, hours_through):
    if start > end or not start <= hours_through <= end:
        raise ValueError("Invalid calendar or reviewed-hours range")
    days, hours = [], []
    day = start
    while day <= end:
        opened = day.weekday() < 5 and day not in holidays
        days.append({"date": str(day), "is_open": int(opened),
                     "reason": holidays.get(day, "weekend" if day.weekday() >= 5 else "regular"),
                     "source_ref": HOLIDAY_SOURCE})
        unresolved = day in UNCONFIRMED_CLOSES and day not in SPECIAL_CLOSES
        if opened and day <= hours_through and not unresolved:
            close, source = SPECIAL_CLOSES.get(day, ("15:30", REGULAR_SOURCE))
            hours.append({"date": str(day), "regular_close_at": f"{day}T{close}:00+09:00",
                          "available_at": "", "source_ref": source})
        day += timedelta(days=1)
    return days, hours


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        # Calendar versions hash raw bytes shared with MARIA. Never use the
        # csv module's CRLF default, which Git may normalize on Linux checkout.
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--hours-through", "--hours-reviewed-through", dest="hours_through",
                        type=date.fromisoformat, required=True,
                        help="Official regular-hours coverage; unresolved special dates stay blocked")
    args = parser.parse_args()
    years = (2023, 2024, 2025, 2026)
    holidays, sources = read_holidays(args.source_dir, years)
    start, end = date(min(years), 1, 1), date(max(years), 12, 31)
    days, hours = calendar_rows(start, end, holidays, args.hours_through)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    dates_path, hours_path = args.output_dir / "open-days.csv", args.output_dir / "trading-hours.csv"
    write_csv(dates_path, ["date", "is_open", "reason", "source_ref"], days)
    write_csv(hours_path, ["date", "regular_close_at", "available_at", "source_ref"], hours)
    report = {"source": HOLIDAY_SOURCE, "sources": sources, "start": str(start), "end": str(end),
              "hours_through": str(args.hours_through),
              "hours_policy": "official_regular_hours_with_unconfirmed_exceptions",
              "unconfirmed_special_sessions": [
                  {"date": str(day), "reason": "exam_day_exchange_hours_unconfirmed", "source_ref": source}
                  for day, source in sorted(UNCONFIRMED_CLOSES.items())
                  if start <= day <= args.hours_through and day.weekday() < 5
                  and day not in holidays and day not in SPECIAL_CLOSES],
              "serialization": {"encoding": "utf-8", "line_ending": "LF"},
              "calendar_days": len(days), "open_days": sum(r["is_open"] for r in days),
              "hours_rows": len(hours), "unknown_hours_open_days": sum(r["is_open"] for r in days) - len(hours),
              "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (dates_path, hours_path)},
              "limits": ["User-provided KRX downloads; no authenticated origin signature.",
                         "Special hours reference KRX notices republished by securities firms.",
                         "Official regular hours apply to verified open dates; special notices override them.",
                         "Unconfirmed special-session hours remain absent, never inferred from the exam date.",
                         "available_at intentionally empty: no KIS finality guarantee."]}
    (args.output_dir / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()
