"""Read days from Google Health and write them to intervals.icu.

A sync is idempotent: the same days always produce the same records, and
intervals.icu simply overwrites those fields with the same values. Fields
with no data are not sent, so they never erase existing values.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

import httpx

from ghti.config import Settings
from ghti.crypto import Keys
from ghti.errors import GhtiError, GoogleHealthError
from ghti.google_health import GoogleHealthClient
from ghti.intervals import IntervalsClient
from ghti.mapping import FIELDS, METRICS, merge, to_wellness_records
from ghti.oauth import StoredToken, TokenStore, refresh_access_token

WINDOW_DAYS = 90  # long ranges are processed (and pushed) 90 days at a time


@dataclass
class SyncReport:
    start: date
    end: date
    days: dict[date, dict] = field(default_factory=dict)
    records_sent: int = 0
    ok: set[str] = field(default_factory=set)
    failed: dict[str, str] = field(default_factory=dict)
    skipped: set[str] = field(default_factory=set)
    token_updated: bool = False
    dry_run: bool = False

    def text(self, show_values: bool = False) -> str:
        verb = "Would send" if self.dry_run else "Sent"
        lines = [f"{verb} {self.records_sent} day(s) to intervals.icu ({self.start} to {self.end})."]
        for day in sorted(self.days):
            values = self.days[day]
            shown = ", ".join(f"{k}={values[k]}" if show_values else k for k in FIELDS if k in values)
            lines.append(f"  {day}: {shown or '(no data)'}")
        lines += [f"  skipped {name}: permission not granted" for name in sorted(self.skipped)]
        lines += [f"  FAILED {name}: {error}" for name, error in sorted(self.failed.items())]
        return "\n".join(lines)

    def markdown(self, show_values: bool = False) -> str:
        verb = "would be sent" if self.dry_run else "sent"
        out = [
            "## Google Health to intervals.icu",
            "",
            f"{self.records_sent} day(s) {verb}, from {self.start} to {self.end}.",
            "",
            "| Day | " + " | ".join(FIELDS) + " |",
            "|---|" + "---|" * len(FIELDS),
        ]
        for day in sorted(self.days):
            values = self.days[day]
            cells = [str(values[k]) if show_values and k in values else ("yes" if k in values else "") for k in FIELDS]
            out.append(f"| {day} | " + " | ".join(cells) + " |")
        if self.skipped:
            out += ["", "Not authorized (tick every box at the next authorization): " + ", ".join(sorted(self.skipped))]
        if self.failed:
            out += ["", "### Problems"] + [f"- **{name}**: {error}" for name, error in sorted(self.failed.items())]
        return "\n".join(out) + "\n"


def windows(start: date, end: date, size: int = WINDOW_DAYS) -> Iterator[tuple[date, date]]:
    while start <= end:
        stop = min(end, start + timedelta(days=size - 1))
        yield start, stop
        start = stop + timedelta(days=1)


def open_google(settings: Settings, http: httpx.Client) -> tuple[GoogleHealthClient, StoredToken, bool]:
    """Load the stored token, get a fresh access token, save a rotated refresh token."""
    store = TokenStore(settings.token_file, Keys(settings.google_client_secret, settings.intervals_api_key))
    token = store.load()
    access_token, new_refresh = refresh_access_token(settings, token, http)
    updated = False
    if new_refresh and new_refresh != token.refresh_token:
        token = replace(token, refresh_token=new_refresh)
        updated = store.save(token)
    return GoogleHealthClient(http, access_token), token, updated


def collect(
    client: GoogleHealthClient, token: StoredToken, start: date, end: date, report: SyncReport
) -> dict[date, dict]:
    """Fetch every metric for start..end. A failing metric does not stop the others."""
    readings: list[tuple] = []
    for metric in METRICS:
        if token.scopes and metric.scope not in token.scopes:
            report.skipped.add(metric.name)
            continue
        try:
            points = client.fetch(metric, start, end)
        except GoogleHealthError as exc:
            report.failed[metric.name] = str(exc)
            continue
        report.ok.add(metric.name)
        for point in points:
            for reading in metric.extract(point):
                if start <= reading.day <= end:
                    readings.append((metric, reading))
    return merge(readings)


def _check(report: SyncReport) -> None:
    if report.failed and not report.ok:
        details = "\n".join(f"- {name}: {error}" for name, error in sorted(report.failed.items()))
        raise GhtiError("Nothing could be read from Google Health:\n" + details)


def run_sync(settings: Settings, start: date, end: date, http: httpx.Client, dry_run: bool = False) -> SyncReport:
    client, token, updated = open_google(settings, http)
    intervals = IntervalsClient(http, settings.intervals_api_key, settings.intervals_athlete_id)
    report = SyncReport(start, end, token_updated=updated, dry_run=dry_run)
    for window_start, window_end in windows(start, end):
        days = collect(client, token, window_start, window_end, report)
        records = to_wellness_records(days)
        if records and not dry_run:
            intervals.put_wellness(records)  # push each window, so progress is kept
        report.days.update(days)
        report.records_sent += len(records)
    _check(report)
    return report


def export_csv(settings: Settings, start: date, end: date, http: httpx.Client) -> tuple[str, SyncReport]:
    """Same data as a sync, as CSV text (columns use intervals.icu field names)."""
    client, token, updated = open_google(settings, http)
    report = SyncReport(start, end, token_updated=updated, dry_run=True)
    for window_start, window_end in windows(start, end):
        report.days.update(collect(client, token, window_start, window_end, report))
    _check(report)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("date", *FIELDS))
    for day in sorted(report.days):
        values = report.days[day]
        writer.writerow((day.isoformat(), *(values.get(k, "") for k in FIELDS)))
    return buffer.getvalue(), report
