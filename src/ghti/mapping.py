"""The ONLY place that knows Google Health data type names, filter fields and
JSON field names, and how they become intervals.icu wellness fields.

Sources (read on 2026-10-22):
- https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints
- https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints/list
- https://developers.google.com/health/aggregate-data
- intervals.icu OpenAPI: https://intervals.icu/api/v1/docs (schema "Wellness")

TO VERIFY WITH REAL DATA: everything marked
with this tag was written from the documentation only, without a real response.
If Google answers differently, fix it here; the rest of the code does not change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

SLEEP_SCOPE = "https://www.googleapis.com/auth/googlehealth.sleep.readonly"
METRICS_SCOPE = "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly"
ACTIVITY_SCOPE = "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly"

# intervals.icu wellness fields we may write, in CSV column order.
FIELDS = ("sleepSecs", "restingHR", "hrv", "weight", "spO2", "steps", "vo2max", "respiration")


@dataclass(frozen=True)
class Reading:
    day: date
    field: str
    value: float | int
    at: str = ""  # sort key; for "last" metrics the newest reading wins


@dataclass(frozen=True)
class Metric:
    name: str  # for messages
    data_type: str  # Google dataType id used in the URL
    method: str  # "list", "reconcile" or "dailyRollUp"
    filter_field: str | None  # AIP-160 filter path; None for dailyRollUp
    page_size: int
    scope: str
    combine: str  # how readings of the same day merge: "sum" or "last"
    extract: Callable[[dict], list[Reading]]


# --- helpers -----------------------------------------------------------------


def _number(value: Any) -> float | None:
    """Numbers may arrive as JSON numbers or as strings (int64 fields)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(obj: Any) -> date | None:
    """google.type.Date {"year", "month", "day"} -> date."""
    try:
        return date(int(obj["year"]), int(obj["month"]), int(obj["day"]))
    except (TypeError, KeyError, ValueError):
        return None


def _offset(duration: Any) -> timedelta:
    """Protobuf Duration as JSON, e.g. "7200s" or "-3600s"."""
    seconds = _number(str(duration).rstrip("s")) if duration else None
    return timedelta(seconds=seconds or 0)


def _local_day(timestamp: Any, utc_offset: Any) -> date | None:
    """Local calendar day of an RFC 3339 instant plus its UTC offset."""
    if not timestamp:
        return None
    try:
        instant = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (instant.astimezone(UTC) + _offset(utc_offset)).date()


def build_filter(field: str, start: date, end_exclusive: date) -> str:
    """Closed-open day range in AIP-160 syntax (the API accepts >= and <)."""
    return f'{field} >= "{start.isoformat()}" AND {field} < "{end_exclusive.isoformat()}"'


# --- extractors ----------------------------------------------------------------


def extract_sleep(point: dict) -> list[Reading]:
    # TO VERIFY WITH REAL DATA: union key "sleep", "summary.minutesAsleep"
    # (int64 as string), "metadata.nap", "interval.civilEndTime.date" and the
    # fallback "interval.endTime" + "interval.endUtcOffset".
    # The Google Health API has no sleep score field (checked 2026-10-22), so
    # intervals.icu "sleepScore" is not written.
    sleep = point.get("sleep") or {}
    if (sleep.get("metadata") or {}).get("nap"):
        return []  # naps would inflate the nightly total
    minutes = _number((sleep.get("summary") or {}).get("minutesAsleep"))
    interval = sleep.get("interval") or {}
    day = _date((interval.get("civilEndTime") or {}).get("date")) or _local_day(
        interval.get("endTime"), interval.get("endUtcOffset")
    )
    if not minutes or day is None:
        return []
    # A night belongs to the day you wake up, as in Fitbit and intervals.icu.
    return [Reading(day, "sleepSecs", int(round(minutes * 60)), str(interval.get("endTime", "")))]


def _daily(key: str, value_keys: tuple[str, ...], field: str, convert: Callable[[float], float | int]):
    """Extractor for daily summary types: {key: {"date": {...}, value_key: n}}.
    The first value key found wins (later keys are fallbacks)."""

    def extract(point: dict) -> list[Reading]:
        data = point.get(key) or {}
        day = _date(data.get("date"))
        for value_key in value_keys:
            value = _number(data.get(value_key))
            if value is not None and value > 0 and day is not None:
                return [Reading(day, field, convert(value))]
        return []

    return extract


def extract_weight(point: dict) -> list[Reading]:
    # TO VERIFY WITH REAL DATA: union key "weight", "weightGrams",
    # "sampleTime.civilTime.date" (output only) and "sampleTime.physicalTime"/"utcOffset".
    weight = point.get("weight") or {}
    grams = _number(weight.get("weightGrams"))
    sample = weight.get("sampleTime") or {}
    day = _date((sample.get("civilTime") or {}).get("date")) or _local_day(
        sample.get("physicalTime"), sample.get("utcOffset")
    )
    if not grams or day is None:
        return []
    return [Reading(day, "weight", round(grams / 1000, 2), str(sample.get("physicalTime", "")))]


def extract_steps(point: dict) -> list[Reading]:
    # TO VERIFY WITH REAL DATA: dailyRollUp item with "civilStartTime.date"
    # and "steps.countSum" (int64 as string). A sum of 0 usually means "no data"
    # (watch not worn), so it is skipped instead of overwriting intervals.icu.
    day = _date((point.get("civilStartTime") or {}).get("date"))
    count = _number((point.get("steps") or {}).get("countSum"))
    if day is None or not count:
        return []
    return [Reading(day, "steps", int(count))]


# --- the metrics we copy ---------------------------------------------------------

METRICS: tuple[Metric, ...] = (
    Metric(
        name="sleep",
        data_type="sleep",
        # TO VERIFY WITH REAL DATA: "reconcile" is the documented way to merge
        # overlapping sleep logs from several devices; "list" would also work.
        method="reconcile",
        filter_field="sleep.interval.civil_end_time",
        page_size=25,  # the API caps sleep pages at 25
        scope=SLEEP_SCOPE,
        combine="sum",
        extract=extract_sleep,
    ),
    Metric(
        name="resting heart rate",
        data_type="daily-resting-heart-rate",
        method="list",
        # TO VERIFY WITH REAL DATA: snake_case filter names, as used by the
        # official google-health-cli (the docs also show a camelCase example).
        filter_field="daily_resting_heart_rate.date",
        page_size=1000,
        scope=METRICS_SCOPE,
        combine="last",
        # TO VERIFY WITH REAL DATA: "beatsPerMinute" (int64 as string).
        extract=_daily("dailyRestingHeartRate", ("beatsPerMinute",), "restingHR", lambda v: int(round(v))),
    ),
    Metric(
        name="HRV",
        data_type="daily-heart-rate-variability",
        method="list",
        filter_field="daily_heart_rate_variability.date",
        page_size=1000,
        scope=METRICS_SCOPE,
        combine="last",
        # TO VERIFY WITH REAL DATA: intervals.icu "hrv" is rMSSD in ms. We
        # assume "averageHeartRateVariabilityMilliseconds" is the nightly rMSSD
        # shown in the Fitbit app; the deep-sleep rMSSD is only a fallback.
        extract=_daily(
            "dailyHeartRateVariability",
            ("averageHeartRateVariabilityMilliseconds", "deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds"),
            "hrv",
            lambda v: round(v, 1),
        ),
    ),
    Metric(
        name="weight",
        data_type="weight",
        method="list",
        filter_field="weight.sample_time.civil_time",
        page_size=1000,
        scope=METRICS_SCOPE,
        combine="last",
        extract=extract_weight,
    ),
    Metric(
        name="SpO2",
        data_type="daily-oxygen-saturation",
        method="list",
        filter_field="daily_oxygen_saturation.date",
        page_size=1000,
        scope=METRICS_SCOPE,
        combine="last",
        # TO VERIFY WITH REAL DATA: "averagePercentage".
        extract=_daily("dailyOxygenSaturation", ("averagePercentage",), "spO2", lambda v: round(v, 1)),
    ),
    Metric(
        name="steps",
        data_type="steps",
        method="dailyRollUp",
        filter_field=None,
        page_size=1000,
        scope=ACTIVITY_SCOPE,
        combine="last",
        extract=extract_steps,
    ),
    Metric(
        name="VO2max",
        data_type="daily-vo2-max",
        method="list",
        filter_field="daily_vo2_max.date",
        page_size=1000,
        scope=ACTIVITY_SCOPE,
        combine="last",
        # TO VERIFY WITH REAL DATA: union key "dailyVo2Max", value "vo2Max".
        extract=_daily("dailyVo2Max", ("vo2Max",), "vo2max", lambda v: round(v, 1)),
    ),
    Metric(
        name="respiratory rate",
        data_type="daily-respiratory-rate",
        method="list",
        filter_field="daily_respiratory_rate.date",
        page_size=1000,
        scope=METRICS_SCOPE,
        combine="last",
        # TO VERIFY WITH REAL DATA: union key "dailyRespiratoryRate", value "breathsPerMinute".
        extract=_daily("dailyRespiratoryRate", ("breathsPerMinute",), "respiration", lambda v: round(v, 1)),
    ),
)


def merge(readings: list[tuple[Metric, Reading]]) -> dict[date, dict[str, float | int]]:
    """Group readings by day. "sum" adds up (several sleep sessions in a night),
    "last" keeps the newest reading (several weigh-ins in a day)."""
    days: dict[date, dict[str, float | int]] = {}
    newest: dict[tuple[date, str], str] = {}
    for metric, reading in readings:
        fields = days.setdefault(reading.day, {})
        key = (reading.day, reading.field)
        if metric.combine == "sum" and reading.field in fields:
            fields[reading.field] += reading.value
        elif key not in newest or reading.at >= newest[key]:
            fields[reading.field] = reading.value
            newest[key] = reading.at
    return days


def to_wellness_records(days: dict[date, dict[str, float | int]]) -> list[dict]:
    """intervals.icu wellness records ("id" is the ISO date). Empty values are
    left out, so a missing measurement never erases what intervals.icu has."""
    records = []
    for day in sorted(days):
        fields = {k: v for k, v in days[day].items() if k in FIELDS and v not in (None, "")}
        if fields:
            records.append({"id": day.isoformat(), **fields})
    return records
