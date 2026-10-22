import base64
import json
from datetime import date

import httpx
import pytest
import respx

from conftest import API_KEY, DT, GOOGLE, INTERVALS_PUT
from ghti.errors import GoogleHealthError, IntervalsError
from ghti.google_health import GoogleHealthClient
from ghti.intervals import IntervalsClient
from ghti.mapping import METRICS

BY_NAME = {metric.name: metric for metric in METRICS}


# --- intervals.icu -----------------------------------------------------------


def test_intervals_uses_basic_auth_and_chunks(mock_http):
    route = mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))
    records = [{"id": f"2026-01-{i % 28 + 1:02d}", "steps": i} for i in range(250)]
    with httpx.Client() as http:
        client = IntervalsClient(http, API_KEY, "i42")
        client.put_wellness(records)
    assert route.call_count == 3
    expected = "Basic " + base64.b64encode(f"API_KEY:{API_KEY}".encode()).decode()
    assert route.calls[0].request.headers["Authorization"] == expected
    assert [len(json.loads(c.request.content)) for c in route.calls] == [100, 100, 50]


def test_intervals_waits_on_rate_limit(mock_http):
    mock_http.put(INTERVALS_PUT).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200)]
    )
    waits = []
    with httpx.Client() as http:
        IntervalsClient(http, API_KEY, "i42", sleep=waits.append).put_wellness([{"id": "2026-10-01", "steps": 1}])
    assert waits == [7.0]


def test_intervals_bad_key_message_does_not_leak_key(mock_http):
    mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(401, text="unauthorized"))
    with pytest.raises(IntervalsError) as excinfo, httpx.Client() as http:
        IntervalsClient(http, API_KEY, "i42").put_wellness([{"id": "2026-10-01", "steps": 1}])
    assert "INTERVALS_API_KEY" in str(excinfo.value)
    assert API_KEY not in str(excinfo.value)


# --- Google Health -------------------------------------------------------------


def test_google_list_follows_pages_and_sends_filter(mock_http):
    route = mock_http.route(method="GET", host=GOOGLE, path=f"{DT}/daily-resting-heart-rate/dataPoints").mock(
        side_effect=[
            httpx.Response(200, json={"dataPoints": [{"n": 1}], "nextPageToken": "p2"}),
            httpx.Response(200, json={"dataPoints": [{"n": 2}]}),
        ]
    )
    with httpx.Client() as http:
        client = GoogleHealthClient(http, "ya29.x")
        points = client.fetch(BY_NAME["resting heart rate"], date(2026, 10, 20), date(2026, 10, 22))
    assert points == [{"n": 1}, {"n": 2}]
    first, second = (call.request.url.params for call in route.calls)
    assert first["filter"] == (
        'daily_resting_heart_rate.date >= "2026-10-20" AND daily_resting_heart_rate.date < "2026-10-23"'
    )
    assert "pageToken" not in first and second["pageToken"] == "p2"
    assert route.calls[0].request.headers["Authorization"] == "Bearer ya29.x"


def test_google_daily_rollup_splits_long_ranges(mock_http):
    route = mock_http.route(method="POST", host=GOOGLE, path=f"{DT}/steps/dataPoints:dailyRollUp").mock(
        return_value=httpx.Response(200, json={"rollupDataPoints": []})
    )
    with httpx.Client() as http:
        GoogleHealthClient(http, "t").fetch(BY_NAME["steps"], date(2026, 1, 1), date(2026, 4, 30))
    bodies = [json.loads(call.request.content) for call in route.calls]
    assert [b["range"]["start"]["date"] for b in bodies] == [
        {"year": 2026, "month": 1, "day": 1},
        {"year": 2026, "month": 4, "day": 1},
    ]
    assert bodies[-1]["range"]["end"]["date"] == {"year": 2026, "month": 5, "day": 1}
    assert bodies[0]["windowSizeDays"] == 1


def test_google_retries_then_explains_errors(mock_http):
    mock_http.route(host=GOOGLE).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(
                403,
                json={
                    "error": {
                        "code": 403,
                        "status": "PERMISSION_DENIED",
                        "message": "Google Health API has not been used in project 1 before or it is disabled",
                    }
                },
            ),
        ]
    )
    waits = []
    with pytest.raises(GoogleHealthError, match="Enable the Google Health API"), httpx.Client() as http:
        GoogleHealthClient(http, "t", sleep=waits.append).fetch(BY_NAME["weight"], date(2026, 10, 1), date(2026, 10, 1))
    assert waits == [5]


def test_network_is_blocked_during_tests():
    with pytest.raises(respx.models.AllMockedAssertionError), httpx.Client() as http:
        http.get("https://example.com/")
