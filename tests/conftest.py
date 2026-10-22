"""Shared fixtures. Every test runs with respx active, so any HTTP request that
is not explicitly mocked fails: the test suite never touches the network."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

import ghti.config as config
from ghti.crypto import Keys
from ghti.oauth import TOKEN_ENDPOINT, StoredToken, TokenStore

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
CLIENT_ID = "123456-test.apps.googleusercontent.com"
CLIENT_SECRET = "test-client-secret-XYZ"
API_KEY = "test-intervals-key-ABC"
REFRESH_TOKEN = "1//test-refresh-token"
GOOGLE = "health.googleapis.com"
DT = "/v4/users/me/dataTypes"
INTERVALS_PUT = "https://intervals.icu/api/v1/athlete/i42/wellness-bulk"

CLEAR_ENV = (
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "INTERVALS_API_KEY",
    "INTERVALS_ATHLETE_ID",
    "GHTI_TOKEN_FILE",
    "GHTI_TIMEZONE",
    "GHTI_REDIRECT_URL",
    "GITHUB_ACTIONS",
    "GITHUB_STEP_SUMMARY",
    "GITHUB_REPOSITORY",
    "GITHUB_SERVER_URL",
)


@pytest.fixture(autouse=True)
def mock_http():
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    for key in CLEAR_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GHTI_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(config, "utcnow", lambda: NOW)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("INTERVALS_API_KEY", API_KEY)
    monkeypatch.setenv("INTERVALS_ATHLETE_ID", "i42")
    monkeypatch.setenv("GHTI_TOKEN_FILE", str(tmp_path / "state" / "token.enc"))
    monkeypatch.setenv("GHTI_TIMEZONE", "Europe/Rome")
    return config.load_settings()


@pytest.fixture
def github(tmp_path, monkeypatch):
    """Pretend to run on GitHub Actions; returns the job summary file."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/my-health-sync")
    return summary


def keys() -> Keys:
    return Keys(CLIENT_SECRET, API_KEY)


def store_token(settings, age: timedelta = timedelta(days=1)) -> Path:
    obtained = NOW - age
    token = StoredToken(REFRESH_TOKEN, obtained, obtained + timedelta(days=7), [])
    TokenStore(settings.token_file, keys()).save(token)
    return settings.token_file


def mock_refresh(router, status: int = 200, body: dict | None = None):
    body = body if body is not None else {"access_token": "ya29.test-access", "expires_in": 3599}
    return router.post(TOKEN_ENDPOINT).mock(return_value=httpx.Response(status, json=body))


def _date(day: int) -> dict:
    return {"year": 2026, "month": 10, "day": day}


# Shapes follow the v4 reference (DataPoint resource), read on 2026-10-22.
SLEEP = {
    "dataPoints": [
        {
            "dataPointName": "users/me/dataTypes/sleep/dataPoints/a",
            "sleep": {
                "interval": {
                    "startTime": "2026-10-20T21:10:00Z",
                    "startUtcOffset": "7200s",
                    "endTime": "2026-10-21T05:00:00Z",
                    "endUtcOffset": "7200s",
                    "civilEndTime": {"date": _date(21), "time": {"hours": 7}},
                },
                "type": "STAGES",
                "metadata": {"nap": False, "mainSleep": True},
                "summary": {"minutesAsleep": "420", "minutesAwake": "35"},
            },
        },
        {
            "dataPointName": "users/me/dataTypes/sleep/dataPoints/b",
            "sleep": {
                "interval": {"endTime": "2026-10-21T13:30:00Z", "endUtcOffset": "7200s"},
                "metadata": {"nap": True},
                "summary": {"minutesAsleep": "25"},
            },
        },
        {
            "dataPointName": "users/me/dataTypes/sleep/dataPoints/c",
            "sleep": {
                "interval": {
                    "endTime": "2026-10-22T04:30:00Z",
                    "endUtcOffset": "7200s",
                    "civilEndTime": {"date": _date(22)},
                },
                "metadata": {"nap": False},
                "summary": {"minutesAsleep": "390"},
            },
        },
    ]
}
RESTING_HR = {"dataPoints": [{"dailyRestingHeartRate": {"date": _date(21), "beatsPerMinute": "52"}}]}
HRV = {
    "dataPoints": [{"dailyHeartRateVariability": {"date": _date(21), "averageHeartRateVariabilityMilliseconds": 48.27}}]
}
WEIGHT = {
    "dataPoints": [
        {
            "weight": {
                "sampleTime": {"physicalTime": "2026-10-21T05:30:00Z", "utcOffset": "7200s"},
                "weightGrams": 72400,
            }
        },
        {
            "weight": {
                "sampleTime": {"physicalTime": "2026-10-21T18:00:00Z", "utcOffset": "7200s"},
                "weightGrams": 72900,
            }
        },
    ]
}
SPO2 = {
    "dataPoints": [
        {
            "dailyOxygenSaturation": {
                "date": _date(21),
                "averagePercentage": 96.4,
                "lowerBoundPercentage": 93,
                "upperBoundPercentage": 99,
            }
        }
    ]
}
VO2MAX = {"dataPoints": [{"dailyVo2Max": {"date": _date(20), "vo2Max": 47.3, "estimated": True}}]}
RESPIRATION = {"dataPoints": []}
STEPS = {
    "rollupDataPoints": [
        {"civilStartTime": {"date": _date(20)}, "civilEndTime": {"date": _date(21)}, "steps": {"countSum": "8123"}},
        {"civilStartTime": {"date": _date(21)}, "civilEndTime": {"date": _date(22)}, "steps": {"countSum": "10456"}},
        {"civilStartTime": {"date": _date(22)}, "civilEndTime": {"date": _date(23)}, "steps": {"countSum": "0"}},
    ]
}

GOOGLE_ROUTES = {
    ("GET", f"{DT}/sleep/dataPoints:reconcile"): SLEEP,
    ("GET", f"{DT}/daily-resting-heart-rate/dataPoints"): RESTING_HR,
    ("GET", f"{DT}/daily-heart-rate-variability/dataPoints"): HRV,
    ("GET", f"{DT}/weight/dataPoints"): WEIGHT,
    ("GET", f"{DT}/daily-oxygen-saturation/dataPoints"): SPO2,
    ("GET", f"{DT}/daily-vo2-max/dataPoints"): VO2MAX,
    ("GET", f"{DT}/daily-respiratory-rate/dataPoints"): RESPIRATION,
    ("POST", f"{DT}/steps/dataPoints:dailyRollUp"): STEPS,
}


def mock_google(router, overrides: dict | None = None) -> dict:
    """Mock every Google Health endpoint; returns the routes by path."""
    routes = {}
    for (method, path), body in GOOGLE_ROUTES.items():
        response = (overrides or {}).get(path) or httpx.Response(200, json=body)
        routes[path] = router.route(method=method, host=GOOGLE, path=path).mock(return_value=response)
    return routes


def put_bodies(route) -> list:
    return [json.loads(call.request.content) for call in route.calls]
