import json

from app import cli


def test_missing_calendar_fails_before_any_network_call(monkeypatch, capsys):
    monkeypatch.delenv("RISK_CALENDAR_PATH", raising=False)
    monkeypatch.setattr("sys.argv", ["risk", "batch"])
    assert cli.main() == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "CALENDAR_UNAVAILABLE"


def test_batch_cli_exit_status_and_skipped_universe(market, monkeypatch, capsys):
    from conftest import make_prices
    _, now, sessions, repository, _, settings = market
    monkeypatch.setenv("RISK_CALENDAR_PATH", str(settings.calendar))
    monkeypatch.setenv("RISK_DB_PATH", str(repository.path))
    monkeypatch.setenv("RISK_UNIVERSE_PATH", str(settings.universe))
    monkeypatch.setattr("sys.argv", ["risk", "batch", "--as-of", sessions[-1].isoformat()])
    monkeypatch.setattr(cli, "now_kst", lambda: now)

    class Client:
        def __init__(self, *args):
            pass

        def history(self, ticker, start, end):
            assert ticker != "385720"
            return make_prices(sessions)

        def close(self):
            pass

    monkeypatch.setattr(cli, "KisClient", Client)
    assert cli.main() == 0
    body = json.loads(capsys.readouterr().out)
    assert body["updated"] == ["005930", "000660"]
    assert body["skipped"] == [{"ticker": "385720", "code": "code_mismatch"}]
