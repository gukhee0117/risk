import json
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.domain import KST, Price, RiskError
from app.services.calendar import TradingCalendar
from tools.observe_kis_close import observe, plan, read_observations
from tools import observe_kis_close as cli


def setup_observation():
    target = date(2026, 10, 29)
    close = datetime(2026, 10, 29, 15, 30, tzinfo=KST)
    cal = TradingCalendar([(target, close)])
    now = close.replace(hour=15, minute=40)
    return target, cal, now


class Client:
    def __init__(self, prices):
        self.prices, self.calls = prices, []

    def history(self, ticker, start, end):
        self.calls.append((ticker, start, end))
        return self.prices


def test_plan_has_changed_close_offsets_and_no_finality_claim():
    target, _, _ = setup_observation()
    cal = TradingCalendar([(target, datetime(2026, 10, 29, 16, 30, tzinfo=KST))])
    result = plan(cal, target)
    assert result["suggested_observations"][0].endswith("16:40:00+09:00")
    assert result["suggested_observations"][-1].endswith("17:30:00+09:00")
    assert result["provider_finality"] == "NOT_ESTABLISHED"


def test_missing_hours_or_holiday_plan_is_blocked():
    target, _, _ = setup_observation()
    for cal, code in ((TradingCalendar([(target, None)], open_days={target}), "TRADING_HOURS_UNAVAILABLE"),
                      (TradingCalendar([(target, None)]), "INVALID_BATCH_DATE")):
        with pytest.raises(RiskError) as exc:
            plan(cal, target)
        assert exc.value.code == code


def test_before_close_plus_ten_never_calls_kis():
    target, cal, now = setup_observation()
    client = Client([])
    with pytest.raises(RiskError) as exc:
        observe(client, cal, target, ["005930"], "demo", clock=lambda: now.replace(minute=39))
    assert exc.value.code == "BATCH_NOT_DUE"
    assert client.calls == []


def test_same_price_is_observation_not_finality_and_no_raw_values():
    target, cal, now = setup_observation()
    client = Client([Price(target, Decimal("123456.00"), 987654)])
    first = observe(client, cal, target, ["005930"], "demo", clock=lambda: now)
    second = observe(client, cal, target, ["005930"], "demo", clock=lambda: now.replace(minute=45), previous=first)
    assert first[0]["comparison"] == "BASELINE"
    assert second[0]["comparison"] == "UNCHANGED"
    assert second[0]["provider_finality"] == "NOT_ESTABLISHED"
    assert second[0]["observed_at"] == now.replace(minute=45).isoformat()
    assert "123456" not in json.dumps(second) and "987654" not in json.dumps(second)
    assert client.calls == [("005930", target, target)] * 2


@pytest.mark.parametrize("price,volume,changed", [(Decimal(101), 9, ["close"]), (Decimal(100), 10, ["volume"])])
def test_close_and_volume_changes_are_distinguished(price, volume, changed):
    target, cal, now = setup_observation()
    previous = observe(Client([Price(target, Decimal(100), 9)]), cal, target, ["005930"], "demo", clock=lambda: now)
    current = observe(Client([Price(target, price, volume)]), cal, target, ["005930"], "demo", clock=lambda: now, previous=previous)
    assert current[0]["changed_fields"] == changed
    assert current[0]["comparison"] == "CHANGED"


@pytest.mark.parametrize("prices,status", [([], "TARGET_MISSING"), ([Price(date(2026,10,29), Decimal(100), 0)], "ZERO_VOLUME")])
def test_missing_or_zero_volume_is_not_treated_as_usable(prices, status):
    target, cal, now = setup_observation()
    result = observe(Client(prices), cal, target, ["005930"], "demo", clock=lambda: now)
    assert result[0]["status"] == status
    assert result[0]["provider_finality"] == "NOT_ESTABLISHED"


def test_auth_failure_stops_other_tickers_and_does_not_log_payload():
    target, cal, now = setup_observation()
    class Failing:
        calls = 0
        def history(self, *_):
            self.calls += 1
            raise RiskError("KIS_AUTH_FAILED", "secret-provider-payload")
    client = Failing()
    result = observe(client, cal, target, ["005930", "069500"], "demo", clock=lambda: now)
    assert client.calls == 1
    assert result[0]["error_code"] == "KIS_AUTH_FAILED"
    assert "secret-provider-payload" not in json.dumps(result)


def test_different_provider_environment_does_not_compare_hashes():
    target, cal, now = setup_observation()
    client = Client([Price(target, Decimal(100), 9)])
    previous = observe(client, cal, target, ["005930"], "demo", clock=lambda: now)
    result = observe(client, cal, target, ["005930"], "real", clock=lambda: now, previous=previous)
    assert result[0]["comparison"] == "BASELINE"


def test_broken_log_is_preserved_and_rejected(tmp_path):
    path = tmp_path / "observation.jsonl"
    original = '{"ticker":'
    path.write_text(original, encoding="utf-8")
    with pytest.raises(RiskError) as exc:
        read_observations(path)
    assert exc.value.code == "INVALID_OBSERVATION_LOG"
    assert path.read_text(encoding="utf-8") == original


def test_plan_cli_does_not_require_keys_or_construct_client(monkeypatch, capsys):
    def forbidden(*_):
        pytest.fail("plan must not construct KIS client")
    monkeypatch.setattr(cli, "KisClient", forbidden)
    monkeypatch.setattr("sys.argv", ["observe", "plan", "--as-of", "2026-10-30"])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["suggested_observations"][0] == "2026-10-30T15:40:00+09:00"


def test_snapshot_cli_before_window_never_creates_file_or_client(monkeypatch, tmp_path, capsys):
    path = tmp_path / "must-not-exist.jsonl"
    def forbidden(*_):
        pytest.fail("before window: must not construct KIS client")
    monkeypatch.setattr(cli, "KisClient", forbidden)
    monkeypatch.setattr(cli, "now_kst", lambda: datetime(2026, 10, 30, 15, 39, tzinfo=KST))
    monkeypatch.setattr("sys.argv", ["observe", "snapshot", "--as-of", "2026-10-30", "--output", str(path)])
    assert cli.main() == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "BATCH_NOT_DUE"
    assert not path.exists()
