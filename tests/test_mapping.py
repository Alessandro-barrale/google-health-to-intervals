from datetime import date

from conftest import HRV, RESTING_HR, SLEEP, SPO2, STEPS, VO2MAX, WEIGHT
from ghti.mapping import (
    METRICS,
    build_filter,
    extract_sleep,
    extract_steps,
    extract_weight,
    merge,
    to_wellness_records,
)

BY_NAME = {metric.name: metric for metric in METRICS}


def readings(name: str, payload: dict, key: str = "dataPoints"):
    metric = BY_NAME[name]
    return [(metric, r) for point in payload[key] for r in metric.extract(point)]


def test_sleep_uses_wake_up_day_and_skips_naps():
    found = [r for p in SLEEP["dataPoints"] for r in extract_sleep(p)]
    assert [(r.day, r.value) for r in found] == [(date(2026, 10, 21), 25200), (date(2026, 10, 22), 23400)]


def test_sleep_falls_back_to_end_time_plus_offset():
    point = {
        "sleep": {
            "interval": {"endTime": "2026-10-21T23:30:00Z", "endUtcOffset": "7200s"},
            "summary": {"minutesAsleep": "60"},
        }
    }
    assert extract_sleep(point)[0].day == date(2026, 10, 22)


def test_sleep_sessions_of_the_same_night_are_summed():
    first = {
        "sleep": {
            "interval": {"civilEndTime": {"date": {"year": 2026, "month": 10, "day": 21}}},
            "summary": {"minutesAsleep": "200"},
        }
    }
    second = {
        "sleep": {
            "interval": {"civilEndTime": {"date": {"year": 2026, "month": 10, "day": 21}}},
            "summary": {"minutesAsleep": "180"},
        }
    }
    metric = BY_NAME["sleep"]
    days = merge([(metric, r) for p in (first, second) for r in extract_sleep(p)])
    assert days[date(2026, 10, 21)]["sleepSecs"] == 380 * 60


def test_daily_values_are_converted():
    assert readings("resting heart rate", RESTING_HR)[0][1].value == 52
    assert readings("HRV", HRV)[0][1].value == 48.3
    assert readings("SpO2", SPO2)[0][1].value == 96.4
    assert readings("VO2max", VO2MAX)[0][1].value == 47.3


def test_hrv_falls_back_to_deep_sleep_rmssd():
    point = {
        "dailyHeartRateVariability": {
            "date": {"year": 2026, "month": 10, "day": 1},
            "deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds": 61,
        }
    }
    assert BY_NAME["HRV"].extract(point)[0].value == 61


def test_weight_keeps_the_latest_measurement_of_the_day_in_kg():
    days = merge(readings("weight", WEIGHT))
    assert days == {date(2026, 10, 21): {"weight": 72.9}}
    assert extract_weight({"weight": {}}) == []


def test_steps_zero_means_no_data():
    found = [r for p in STEPS["rollupDataPoints"] for r in extract_steps(p)]
    assert [(r.day.day, r.value) for r in found] == [(20, 8123), (21, 10456)]


def test_missing_or_broken_values_are_ignored():
    assert (
        BY_NAME["resting heart rate"].extract(
            {"dailyRestingHeartRate": {"date": {"year": 2026, "month": 10, "day": 1}}}
        )
        == []
    )
    assert BY_NAME["SpO2"].extract({"dailyOxygenSaturation": {"averagePercentage": "n/a"}}) == []
    assert BY_NAME["VO2max"].extract({}) == []


def test_records_skip_empty_and_unknown_fields():
    days = {date(2026, 10, 2): {"steps": 10, "weight": None, "other": 1}, date(2026, 10, 1): {"hrv": ""}}
    assert to_wellness_records(days) == [{"id": "2026-10-02", "steps": 10}]


def test_filter_syntax():
    assert build_filter("weight.sample_time.civil_time", date(2026, 10, 1), date(2026, 10, 3)) == (
        'weight.sample_time.civil_time >= "2026-10-01" AND weight.sample_time.civil_time < "2026-10-03"'
    )
