"""intervals.icu client: one bulk update of wellness records.

PUT /api/v1/athlete/{id}/wellness-bulk with HTTP Basic auth: user "API_KEY",
password = the personal API key (Settings > Developer Settings on intervals.icu).
Limits: 5,000 requests per day and 2,500 per 15 minutes. A normal sync sends
one request; a one-year backfill about four.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from ghti.errors import IntervalsError

BASE_URL = "https://intervals.icu/api/v1"
CHUNK_SIZE = 100  # days per request
MAX_REQUESTS_PER_RUN = 100  # far below the daily limit, even with 4 runs a day


class IntervalsClient:
    def __init__(
        self,
        http: httpx.Client,
        api_key: str,
        athlete_id: str,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.http = http
        self.auth = httpx.BasicAuth("API_KEY", api_key)
        self.athlete_id = athlete_id
        self.max_retries = max_retries
        self.sleep = sleep
        self.requests = 0

    def put_wellness(self, records: list[dict]) -> None:
        """Send records (each with "id" = ISO date), CHUNK_SIZE at a time."""
        url = f"{BASE_URL}/athlete/{self.athlete_id}/wellness-bulk"
        for i in range(0, len(records), CHUNK_SIZE):
            self._put(url, records[i : i + CHUNK_SIZE])

    def _put(self, url: str, chunk: list[dict]) -> None:
        for attempt in range(self.max_retries + 1):
            if self.requests >= MAX_REQUESTS_PER_RUN:
                raise IntervalsError("Too many intervals.icu requests in one run; stopping to respect the limits.")
            self.requests += 1
            response = self.http.put(url, json=chunk, auth=self.auth)
            if response.status_code < 400:
                return
            if response.status_code in (429, 502, 503, 504) and attempt < self.max_retries:
                retry_after = response.headers.get("Retry-After", "")
                self.sleep(float(retry_after) if retry_after.isdigit() else 30 * (attempt + 1))
                continue
            raise _error(response, self.athlete_id)


def _error(response: httpx.Response, athlete_id: str) -> IntervalsError:
    code = response.status_code
    if code in (401, 403):
        return IntervalsError(
            f"intervals.icu refused the request (HTTP {code}). Check INTERVALS_API_KEY "
            f"and INTERVALS_ATHLETE_ID (now {athlete_id!r}; '0' means the key's owner)."
        )
    if code == 404:
        return IntervalsError(f"intervals.icu athlete {athlete_id!r} not found (HTTP 404). Check INTERVALS_ATHLETE_ID.")
    if code == 429:
        return IntervalsError("intervals.icu rate limit reached (HTTP 429). The next run will try again.")
    return IntervalsError(f"intervals.icu error HTTP {code}: {response.text[:200]}")
