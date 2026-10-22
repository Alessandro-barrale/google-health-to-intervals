from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx

from conftest import (
    CLIENT_SECRET,
    REFRESH_TOKEN,
    keys,
    mock_google,
    mock_refresh,
    store_token,
)
from ghti.cli import main
from ghti.oauth import REDIRECT_URI, TOKEN_ENDPOINT, TokenStore


def printed_link(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("https://accounts.google.com/"))


def test_auth_url_then_auth_finish(settings, mock_http, capsys):
    assert main(["auth-url"]) == 0
    link = printed_link(capsys.readouterr().out)
    state = parse_qs(urlsplit(link).query)["state"][0]

    route = mock_http.post(TOKEN_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"access_token": "ya29.x", "refresh_token": REFRESH_TOKEN})
    )
    address = f"{REDIRECT_URI}?state={state}&code=4/0AbcDef&scope=x"
    assert main(["auth-finish", address]) == 0

    out = capsys.readouterr().out
    assert "Google authorization saved. It expires on 2026-10-29 09:00 UTC." in out
    assert REFRESH_TOKEN not in out
    assert "code_verifier" in route.calls[0].request.content.decode()
    assert TokenStore(settings.token_file, keys()).load().refresh_token == REFRESH_TOKEN


def test_auth_finish_reads_address_from_env_on_github(settings, mock_http, github, monkeypatch, capsys):
    state = keys().make_state(0)
    monkeypatch.setenv("GHTI_REDIRECT_URL", f"{REDIRECT_URI}?state={state}&code=4/0AbcDef")
    mock_http.post(TOKEN_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"access_token": "ya29.x", "refresh_token": REFRESH_TOKEN})
    )
    assert main(["auth-finish"]) == 0
    assert "::add-mask::4/0AbcDef" in capsys.readouterr().out
    assert "Google authorization saved" in github.read_text(encoding="utf-8")


def test_auth_finish_with_denied_access(settings, capsys):
    assert main(["auth-finish", f"{REDIRECT_URI}?error=access_denied"]) == 3
    assert "not granted" in capsys.readouterr().err


def test_status_reminds_on_day_six(settings, github, capsys):
    store_token(settings, age=timedelta(days=6))
    assert main(["status", "--fail-on-reminder"]) == 1
    summary = github.read_text(encoding="utf-8")
    assert "Reminder: the Google authorization expires tomorrow" in summary
    assert "[Authorize Google Health](https://accounts.google.com/" in summary
    assert main(["status"]) == 0  # without the flag the reminder does not fail the job


def test_status_ok_early_in_the_week(settings, github, capsys):
    store_token(settings, age=timedelta(days=2))
    assert main(["status", "--fail-on-reminder"]) == 0
    assert "expires on 2026-10-27" in capsys.readouterr().out
    assert "Reminder" not in github.read_text(encoding="utf-8")


def test_status_when_expired(settings, capsys):
    store_token(settings, age=timedelta(days=7, hours=1))
    assert main(["status"]) == 3
    assert "expired" in capsys.readouterr().out


def test_export_csv(settings, mock_http, tmp_path, capsys):
    store_token(settings)
    mock_refresh(mock_http)
    mock_google(mock_http)
    out_file = tmp_path / "wellness.csv"
    assert main(["export-csv", "--days", "3", "-o", str(out_file)]) == 0
    lines = out_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "date,sleepSecs,restingHR,hrv,weight,spO2,steps,vo2max,respiration"
    assert lines[2] == "2026-10-21,25200,52,48.3,72.9,96.4,10456,,"
    assert len(lines) == 4


def test_missing_settings_are_listed(capsys):
    assert main(["sync"]) == 2
    err = capsys.readouterr().err
    assert "GOOGLE_CLIENT_ID" in err and "INTERVALS_API_KEY" in err


def test_unconfigured_template_repository_is_skipped(github, capsys):
    assert main(["sync", "--skip-if-unconfigured"]) == 0
    assert "Not configured yet" in capsys.readouterr().out


def test_config_file_is_used_on_a_computer(tmp_path, monkeypatch, capsys):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.env").write_text(
        f'GOOGLE_CLIENT_ID=abc.apps.googleusercontent.com\nGOOGLE_CLIENT_SECRET="{CLIENT_SECRET}"\nINTERVALS_API_KEY=k\n',
        encoding="utf-8",
    )
    assert main(["auth-url"]) == 0
    assert "client_id=abc.apps.googleusercontent.com" in capsys.readouterr().out


def test_invalid_timezone(settings, monkeypatch, capsys):
    monkeypatch.setenv("GHTI_TIMEZONE", "Mars/Olympus")
    assert main(["sync"]) == 2
    assert "Europe/Rome" in capsys.readouterr().err


def test_status_in_unconfigured_template_is_skipped(github):
    assert main(["status", "--skip-if-unconfigured", "--fail-on-reminder"]) == 0
