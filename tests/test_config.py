import pytest

from app.config import ROOT, Settings, read_env
from app.domain import RiskError


def test_shared_file_is_selective_and_secrets_not_represented(tmp_path, monkeypatch):
    shared = tmp_path / "shared.env"
    shared.write_text('KIS_APP_KEY="fake-key"\nKIS_APP_SECRET=\'fake-secret\'\nRISK_DB_PATH=wrong.sqlite3\nUNRELATED_PASSWORD=ignored\n', encoding="utf-8")
    local = tmp_path / "risk.env"
    local.write_text("KIS_ENV_FILE=shared.env\nKIS_ENV=demo\nRISK_DB_PATH=data/test.sqlite3\n", encoding="utf-8")
    monkeypatch.setenv("RISK_ENV_FILE", str(local))
    settings = Settings.from_env()
    assert settings.kis_key == "fake-key"
    assert settings.kis_secret == "fake-secret"
    assert settings.database == ROOT / "data/test.sqlite3"
    assert "fake-key" not in repr(settings)
    assert "fake-secret" not in repr(settings)


def test_process_env_wins_and_all_credentials_avoid_shared_dependency(tmp_path, monkeypatch):
    path = tmp_path / "risk.env"
    path.write_text("KIS_ENV_FILE=absent.env\nKIS_APP_KEY=file-key\nKIS_APP_SECRET=file-secret\nKIS_ENV=demo\n", encoding="utf-8")
    monkeypatch.setenv("RISK_ENV_FILE", str(path))
    monkeypatch.setenv("KIS_APP_KEY", "env-key")
    monkeypatch.setenv("KIS_APP_SECRET", "env-secret")
    monkeypatch.setenv("KIS_ENV", "real")
    settings = Settings.from_env()
    assert (settings.kis_key, settings.kis_secret, settings.kis_environment) == ("env-key", "env-secret", "real")


def test_missing_explicit_file_fails_without_disclosing_path(monkeypatch, tmp_path):
    monkeypatch.setenv("RISK_ENV_FILE", str(tmp_path / "private-file.env"))
    with pytest.raises(RiskError) as error:
        Settings.from_env()
    assert error.value.code == "ENV_FILE_UNAVAILABLE"
    assert "private-file" not in str(error.value)


def test_shared_file_missing_fails_safely(monkeypatch, tmp_path):
    monkeypatch.setenv("KIS_ENV_FILE", str(tmp_path / "absent.env"))
    with pytest.raises(RiskError):
        Settings.from_env()


def test_literal_env_reader_preserves_hash_and_windows_path(tmp_path):
    path = tmp_path / "test.env"
    path.write_text('export KIS_APP_KEY="fake#key" # comment\nKIS_APP_SECRET=fake#secret\nRISK_DB_PATH=C:\\data\\risk.sqlite3 # comment\nOTHER=ignored\n', encoding="utf-8-sig")
    values = read_env(path)
    assert values == {"KIS_APP_KEY": "fake#key", "KIS_APP_SECRET": "fake#secret", "RISK_DB_PATH": "C:\\data\\risk.sqlite3"}


def test_malformed_quote_does_not_echo_secret(tmp_path):
    path = tmp_path / "test.env"
    path.write_text('KIS_APP_KEY="never-echo', encoding="utf-8")
    with pytest.raises(RiskError) as error:
        read_env(path)
    assert "never-echo" not in str(error.value)
