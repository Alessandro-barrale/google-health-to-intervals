"""Small client for the Google Health API v4 (read only).

Endpoints (https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints):
- GET  /v4/users/me/dataTypes/{type}/dataPoints             (list)
- GET  /v4/users/me/dataTypes/{type}/dataPoints:reconcile   (reconcile)
- POST /v4/users/me/dataTypes/{type}/dataPoints:dailyRollUp (daily totals)
The rate limit is 300 requests per minute per user; a sync uses about ten.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date, timedelta

import httpx

from ghti.errors import GoogleHealthError
from ghti.mapping import Metric, build_filter

BASE_URL = "https://health.googleapis.com/v4/users/me/dataTypes"
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ROLLUP_DAYS = 90  # documented maximum range of dailyRollUp for steps
MAX_PAGES = 200  # safety net against a pagination loop


class GoogleHealthClient:
    def __init__(
        self,
        http: httpx.Client,
        access_token: str,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.http = http
        self.headers = {"Authorization": f"Bearer {access_token}"}
        self.max_retries = max_retries
        self.sleep = sleep
        self.requests = 0

    def _call(self, method: str, url: str, **kwargs) -> dict:
        for attempt in range(self.max_retries + 1):
            self.requests += 1
            response = self.http.request(method, url, headers=self.headers, **kwargs)
            if response.status_code < 400:
                return response.json() if response.content else {}
            if response.status_code in RETRY_STATUSES and attempt < self.max_retries:
                retry_after = _number(response.headers.get("Retry-After"))
                self.sleep(retry_after if retry_after is not None else 2**attempt * 5)
                continue
            raise _error(response)
        raise AssertionError("unreachable")

    def fetch(self, metric: Metric, start: date, end: date) -> list[dict]:
        """All data points of `metric` for the days start..end (inclusive)."""
        if metric.method == "dailyRollUp":
            return self._daily_rollup(metric, start, end)
        suffix = ":reconcile" if metric.method == "reconcile" else ""
        url = f"{BASE_URL}/{metric.data_type}/dataPoints{suffix}"
        params = {
            "filter": build_filter(metric.filter_field, start, end + timedelta(days=1)),
            "pageSize": metric.page_size,
        }
        points: list[dict] = []
        for _ in range(MAX_PAGES):
            data = self._call("GET", url, params=params)
            points.extend(data.get("dataPoints", []))
            token = data.get("nextPageToken")
            if not token:
                break
            params["pageToken"] = token
        return points

    def _daily_rollup(self, metric: Metric, start: date, end: date) -> list[dict]:
        url = f"{BASE_URL}/{metric.data_type}/dataPoints:dailyRollUp"
        points: list[dict] = []
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(end, chunk_start + timedelta(days=MAX_ROLLUP_DAYS - 1))
            body: dict = {
                "range": {"start": _civil(chunk_start), "end": _civil(chunk_end + timedelta(days=1))},
                "windowSizeDays": 1,
                "pageSize": metric.page_size,
            }
            for _ in range(MAX_PAGES):
                data = self._call("POST", url, json=body)
                points.extend(data.get("rollupDataPoints", []))
                token = data.get("nextPageToken")
                if not token:
                    break
                body["pageToken"] = token
            chunk_start = chunk_end + timedelta(days=1)
        return points


def _civil(day: date) -> dict:
    """CivilDateTime at local midnight."""
    return {
        "date": {"year": day.year, "month": day.month, "day": day.day},
        "time": {"hours": 0, "minutes": 0, "seconds": 0, "nanos": 0},
    }


def _number(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _error(response: httpx.Response) -> GoogleHealthError:
    """Turn a Google error into a message that says what to do."""
    try:
        error = response.json().get("error", {})
        message = str(error.get("message", "")) or response.reason_phrase
        status = str(error.get("status", ""))
    except (ValueError, AttributeError):
        message, status = response.text[:200], ""
    code = response.status_code
    hint = ""
    if code == 403 and ("disabled" in message.lower() or "has not been used" in message.lower()):
        hint = " Enable the Google Health API in your Google Cloud project."
    elif code == 403:
        hint = " The permission for this data was not granted: authorize again and tick every box."
    elif code == 401:
        hint = " The access token was refused: authorize again."
    elif code == 429:
        hint = " Google rate limit reached: try again later."
    return GoogleHealthError(f"Google Health API error {code} {status}: {message}.{hint}".strip(), code)
