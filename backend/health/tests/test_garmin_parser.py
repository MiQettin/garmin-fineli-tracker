import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from django.test import SimpleTestCase

from health.garmin_parser import (
    _body_battery_range_from_stress,
    parse_daily_health,
    parse_hrv_summary,
    parse_sleep_summary,
)

# backend/data/ is gitignored personal export data. These tests read the
# real captured Garmin responses from that folder directly rather than
# committing personal health JSON into tracked test source (see Issues in
# the task report for the portability trade-off this implies).
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "data" / "garmin_health_debug"
FIXTURE_DATE = "2026-08-31"


def _load_fixture(name):
    path = FIXTURE_DIR / f"{FIXTURE_DATE}_{name}.json"
    if not path.exists():
        raise unittest.SkipTest(
            f"Captured Garmin fixture not found: {path}. "
            "Run backend/tools/inspect_garmin_health.py manually to regenerate it "
            "(backend/data/ is gitignored personal data, so it is not checked in)."
        )
    with path.open(encoding="utf-8") as f:
        return json.load(f)


class ParseSleepSummaryTests(SimpleTestCase):
    def test_maps_daily_sleep_dto_fields(self):
        data = _load_fixture("sleep")
        result = parse_sleep_summary(data)
        daily = data["dailySleepDTO"]

        self.assertEqual(result["garmin_sleep_id"], daily["id"])
        self.assertEqual(result["wake_date"], date.fromisoformat(daily["calendarDate"]))
        self.assertEqual(
            result["sleep_start"],
            datetime.fromtimestamp(daily["sleepStartTimestampGMT"] / 1000, tz=timezone.utc),
        )
        self.assertEqual(
            result["sleep_end"],
            datetime.fromtimestamp(daily["sleepEndTimestampGMT"] / 1000, tz=timezone.utc),
        )
        self.assertEqual(result["sleep_start"].tzinfo, timezone.utc)
        self.assertEqual(result["total_sleep_seconds"], daily["sleepTimeSeconds"])
        self.assertEqual(result["deep_sleep_seconds"], daily["deepSleepSeconds"])
        self.assertEqual(result["light_sleep_seconds"], daily["lightSleepSeconds"])
        self.assertEqual(result["rem_sleep_seconds"], daily["remSleepSeconds"])
        self.assertEqual(result["sleep_score"], daily["sleepScores"]["overall"]["value"])
        self.assertEqual(result["sleep_score_qualifier"], daily["sleepScores"]["overall"]["qualifierKey"])
        self.assertEqual(result["avg_heart_rate"], int(daily["avgHeartRate"]))
        self.assertEqual(result["avg_stress"], daily["avgSleepStress"])
        self.assertEqual(result["avg_respiration"], daily["averageRespirationValue"])
        self.assertEqual(result["avg_spo2"], daily["averageSpO2Value"])
        self.assertEqual(result["lowest_spo2"], float(daily["lowestSpO2Value"]))
        self.assertEqual(result["sleep_need_baseline_minutes"], daily["sleepNeed"]["baseline"])
        self.assertEqual(result["sleep_need_actual_minutes"], daily["sleepNeed"]["actual"])


class ParseHrvSummaryTests(SimpleTestCase):
    def test_none_input_returns_none(self):
        self.assertIsNone(parse_hrv_summary(None))

    def test_maps_hrv_summary_fields(self):
        data = _load_fixture("hrv")
        result = parse_hrv_summary(data)
        summary = data["hrvSummary"]

        self.assertEqual(result["night_date"], date.fromisoformat(summary["calendarDate"]))
        self.assertEqual(result["weekly_avg"], summary["weeklyAvg"])
        self.assertEqual(result["last_night_avg"], summary["lastNightAvg"])
        self.assertEqual(result["last_night_5min_high"], summary["lastNight5MinHigh"])
        self.assertEqual(result["baseline_low"], summary["baseline"]["balancedLow"])
        self.assertEqual(result["baseline_upper"], summary["baseline"]["balancedUpper"])
        self.assertEqual(result["status"], summary["status"])
        self.assertEqual(result["marker_value"], summary["baseline"]["markerValue"])


class ParseDailyHealthTests(SimpleTestCase):
    def test_maps_stress_respiration_spo2_and_body_battery(self):
        target_date = date.fromisoformat(FIXTURE_DATE)
        stress = _load_fixture("stress")
        body_battery = _load_fixture("body_battery")
        respiration = _load_fixture("respiration")
        spo2 = _load_fixture("spo2")

        result = parse_daily_health(target_date, stress, body_battery, respiration, spo2)

        self.assertEqual(result["stress_avg"], stress["avgStressLevel"])
        self.assertEqual(result["stress_max"], stress["maxStressLevel"])
        self.assertEqual(result["respiration_avg_waking"], respiration["avgWakingRespirationValue"])
        self.assertEqual(result["respiration_min"], respiration["lowestRespirationValue"])
        self.assertEqual(result["respiration_max"], respiration["highestRespirationValue"])
        self.assertEqual(result["spo2_avg"], spo2["averageSpO2"])
        self.assertEqual(result["spo2_min"], float(spo2["lowestSpO2"]))
        self.assertEqual(result["spo2_latest"], float(spo2["latestSpO2"]))
        self.assertEqual(
            result["spo2_latest_at"],
            datetime.fromisoformat(spo2["latestSpO2TimestampGMT"]).replace(tzinfo=timezone.utc),
        )
        self.assertEqual(result["spo2_latest_at"].tzinfo, timezone.utc)

        matching_day = next(e for e in body_battery if e["date"] == FIXTURE_DATE)
        self.assertEqual(result["body_battery_charged"], matching_day["charged"])
        self.assertEqual(result["body_battery_drained"], matching_day["drained"])

        # Independently resolve columns by descriptor name (not position) to
        # prove the parser's start/end/min/max match the real dense series.
        descriptors = stress["bodyBatteryValueDescriptorsDTOList"]
        columns = {d["bodyBatteryValueDescriptorKey"]: d["bodyBatteryValueDescriptorIndex"] for d in descriptors}
        ts_idx = columns["timestamp"]
        level_idx = columns["bodyBatteryLevel"]
        rows = stress["bodyBatteryValuesArray"]
        ordered = sorted(rows, key=lambda r: r[ts_idx])

        self.assertEqual(result["body_battery_start"], ordered[0][level_idx])
        self.assertEqual(result["body_battery_end"], ordered[-1][level_idx])
        self.assertEqual(result["body_battery_min"], min(r[level_idx] for r in rows))
        self.assertEqual(result["body_battery_max"], max(r[level_idx] for r in rows))

    def test_body_battery_extraction_is_column_order_independent(self):
        # Descriptor order deliberately scrambled relative to real Garmin
        # responses, proving columns are resolved by name, not position.
        stress_response = {
            "bodyBatteryValueDescriptorsDTOList": [
                {"bodyBatteryValueDescriptorIndex": 2, "bodyBatteryValueDescriptorKey": "timestamp"},
                {"bodyBatteryValueDescriptorIndex": 0, "bodyBatteryValueDescriptorKey": "bodyBatteryStatus"},
                {"bodyBatteryValueDescriptorIndex": 1, "bodyBatteryValueDescriptorKey": "bodyBatteryLevel"},
            ],
            "bodyBatteryValuesArray": [
                ["MEASURED", 40, 1000],
                ["MEASURED", 70, 2000],
                ["MEASURED", 55, 3000],
            ],
        }

        start, end, low, high = _body_battery_range_from_stress(stress_response)

        self.assertEqual(start, 40)
        self.assertEqual(end, 55)
        self.assertEqual(low, 40)
        self.assertEqual(high, 70)

    def test_missing_body_battery_day_and_empty_responses_return_none(self):
        target_date = date(2000, 1, 1)
        result = parse_daily_health(target_date, {}, [], {}, {})

        self.assertEqual(result["date"], target_date)
        self.assertIsNone(result["stress_avg"])
        self.assertIsNone(result["body_battery_charged"])
        self.assertIsNone(result["body_battery_drained"])
        self.assertIsNone(result["body_battery_start"])
        self.assertIsNone(result["body_battery_end"])
        self.assertIsNone(result["body_battery_min"])
        self.assertIsNone(result["body_battery_max"])
