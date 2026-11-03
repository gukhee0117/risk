from datetime import date

import pytest

from tools.compare_price_basis import comparison, public_row
from tools import compare_price_basis as tool


def payload(rows, count=1):
    return {"response": {"header": {"resultCode": "00"}, "body": {
        "totalCount": count, "items": {"item": rows}}}}


def row(**changes):
    return {"basDt": "20261030", "srtnCd": "A005930", "clpr": "220500", "trqu": "100", **changes}


def test_public_singleton_and_list():
    for rows in (row(), [row()]):
        assert public_row(payload(rows), "005930", date(2026, 10, 30))["close"] == "220500"


@pytest.mark.parametrize("changes", [{"basDt": "20261029"}, {"srtnCd": "000660"}, {"clpr": "NaN"}, {"clpr": "0"}, {"trqu": "-1"}])
def test_reject_wrong_or_invalid_price(changes):
    with pytest.raises(ValueError):
        public_row(payload([row(**changes)]), "005930", date(2026, 10, 30))


def test_empty_is_missing_not_zero():
    assert public_row(payload([], 0), "005930", date(2026, 10, 30)) is None
    assert comparison(None, {"close": "1"}) == "MISSING"


def test_duplicate_is_not_silently_selected():
    with pytest.raises(ValueError):
        public_row(payload([row(), row()], 2), "005930", date(2026, 10, 30))


def test_comparison_does_not_claim_finality():
    assert comparison({"close": "1.0"}, {"close": "1"}) == "MATCH"
    assert comparison({"close": "2"}, {"close": "1"}) == "DIFFERENT_REVIEW_BASIS"


@pytest.mark.parametrize("command", ["kis", "public"])
def test_future_window_does_not_construct_client(monkeypatch, command):
    from datetime import datetime
    from app.domain import KST
    monkeypatch.setattr("sys.argv", ["price-basis", command])
    monkeypatch.setattr(tool, "now_kst", lambda: datetime(2026, 11, 1, 12, tzinfo=KST))
    monkeypatch.setattr(tool, "KisClient", lambda *a: pytest.fail("network client created"))
    monkeypatch.setattr(tool.httpx, "Client", lambda *a, **kw: pytest.fail("network client created"))
    assert tool.main() == 1


def test_plan_no_network(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["price-basis", "plan"])
    monkeypatch.setattr(tool.httpx, "Client", lambda *a, **kw: pytest.fail("network client created"))
    assert tool.main() == 0
    assert "2026-11-02T13:00:00+09:00" in capsys.readouterr().out


def test_weekend_evidence_is_not_friday_evidence(tmp_path):
    import json
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"environment": "demo", "records": [{"ticker": "005930", "adjustment": "0",
        "rows": [{"stck_bsop_date": "20261030", "stck_clpr": "220500", "acml_vol": "1"}]}]}), encoding="utf-8")
    values = tool.baseline_rows(path, date(2026, 10, 30))
    assert values["005930"]["close"] == "220500"
    assert "102110" not in values
