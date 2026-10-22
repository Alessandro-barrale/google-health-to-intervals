from datetime import timedelta
from urllib.parse import unquote

import httpx

from conftest import (
    API_KEY,
    CLIENT_ID,
    CLIENT_SECRET,
    DT,
    INTERVALS_PUT,
    REFRESH_TOKEN,
    mock_google,
    mock_refresh,
    put_bodies,
    store_token,
)
from ghti.cli import main

EXPECTED = [
    {"id": "2026-10-20", "steps": 8123, "vo2max": 47.3},
    {
        "id": "2026-10-21",
        "sleepSecs": 25200,
        "restingHR": 52,
        "hrv": 48.3,
        "weight": 72.9,
        "spO2": 96.4,
        "steps": 10456,
    },
    {"id": "2026-10-22", "sleepSecs": 23400},
]


def test_sync_sends_mapped_days_to_intervals(settings, mock_http, capsys):
    store_token(settings)
    mock_refresh(mock_http)
    google = mock_google(mock_http)
    put = mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))

    assert main(["sync"]) == 0

    assert put_bodies(put) == [EXPECTED]
    sleep_filter = google[f"{DT}/sleep/dataPoints:reconcile"].calls[0].request.url.params["filter"]
    assert (
        sleep_filter == 'sleep.interval.civil_end_time >= "2026-10-20" AND sleep.interval.civil_end_time < "2026-10-23"'
    )
    out = capsys.readouterr().out
    assert "Sent 3 day(s)" in out
    assert "10456" not in out  # values are not printed unless --show-values


def test_sync_is_idempotent_and_does_not_rewrite_the_token(settings, mock_http):
    token_file = store_token(settings)
    before = token_file.read_bytes()
    mock_refresh(mock_http)
    mock_google(mock_http)
    put = mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))

    assert main(["sync"]) == 0
    assert main(["sync"]) == 0

    first, second = put_bodies(put)
    assert first == second == EXPECTED
    assert token_file.read_bytes() == before


def test_sync_saves_a_rotated_refresh_token(settings, mock_http):
    token_file = store_token(settings)
    before = token_file.read_bytes()
    mock_refresh(mock_http, body={"access_token": "ya29.x", "refresh_token": "1//rotated"})
    mock_google(mock_http)
    mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))
    assert main(["sync"]) == 0
    assert token_file.read_bytes() != before


def test_dry_run_does_not_write(settings, mock_http):
    store_token(settings)
    mock_refresh(mock_http)
    mock_google(mock_http)
    put = mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))
    assert main(["sync", "--dry-run"]) == 0
    assert put.call_count == 0


def test_one_failing_metric_does_not_stop_the_others(settings, mock_http, github):
    store_token(settings)
    mock_refresh(mock_http)
    broken = httpx.Response(
        400, json={"error": {"code": 400, "message": "Invalid filter", "status": "INVALID_ARGUMENT"}}
    )
    mock_google(mock_http, {f"{DT}/daily-heart-rate-variability/dataPoints": broken})
    put = mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))

    assert main(["sync"]) == 0

    sent = put_bodies(put)[0]
    assert "hrv" not in sent[1] and sent[1]["restingHR"] == 52
    assert "HRV" in github.read_text(encoding="utf-8") and "Invalid filter" in github.read_text(encoding="utf-8")


def test_expired_token_fails_with_clear_instructions(settings, mock_http, github, capsys):
    store_token(settings, age=timedelta(days=8))
    mock_refresh(mock_http, 400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
    put = mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))

    assert main(["sync"]) == 3

    assert put.call_count == 0
    summary = github.read_text(encoding="utf-8")
    assert "Google authorization needed" in summary
    assert "expired or was revoked" in summary
    assert "https://github.com/someone/my-health-sync/actions/workflows/reauth.yml" in summary
    link = summary.split("[Authorize Google Health](")[1].split(")")[0]
    assert link.startswith("https://accounts.google.com/o/oauth2/v2/auth?client_id=%")
    assert unquote(link.split("client_id=")[1].split("&")[0]) == CLIENT_ID
    everything = summary + capsys.readouterr().out
    for secret in (CLIENT_SECRET, API_KEY, REFRESH_TOKEN, CLIENT_ID):
        assert secret not in everything


def test_missing_token_asks_for_authorization(settings, mock_http, capsys):
    assert main(["sync"]) == 3
    out = capsys.readouterr().out
    assert "No Google authorization found" in out
    assert "ghti auth-finish" in out


def test_backfill_pushes_window_by_window(settings, mock_http):
    store_token(settings)
    mock_refresh(mock_http)
    google = mock_google(mock_http)
    put = mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))

    assert main(["backfill", "--from", "2026-06-01", "--to", "2026-10-22"]) == 0

    # 144 days -> two 90-day windows: two requests per data type, and every
    # window with data is pushed on its own (the fake data falls in the second).
    assert google[f"{DT}/weight/dataPoints"].call_count == 2
    assert put_bodies(put) == [EXPECTED]


def test_backfill_rejects_reversed_range(settings, capsys):
    assert main(["backfill", "--from", "2026-10-22", "--to", "2026-10-01"]) == 2
    assert "after" in capsys.readouterr().err
