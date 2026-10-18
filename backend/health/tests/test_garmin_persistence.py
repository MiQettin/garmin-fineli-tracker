from datetime import date, datetime, timezone

from django.test import TestCase

from health.garmin_persistence import (
    upsert_daily_health,
    upsert_hrv_summary,
    upsert_sleep_summary,
)
from health.models import DailyHealth, HrvSummary, SleepSummary


def _daily_health_data(**overrides):
    data = {
        "date": date(2026, 8, 31),
        "stress_avg": 21,
        "stress_max": 89,
        "respiration_avg_waking": 14.0,
        "respiration_min": 7.0,
        "respiration_max": 21.0,
        "spo2_avg": 96.0,
        "spo2_min": 86.0,
        "spo2_latest": 97.0,
        "spo2_latest_at": datetime(2026, 8, 31, 21, 0, tzinfo=timezone.utc),
        "body_battery_start": 40,
        "body_battery_end": 55,
        "body_battery_min": None,
        "body_battery_max": 70,
        "body_battery_charged": 0,
        "body_battery_drained": 81,
    }
    data.update(overrides)
    return data


def _sleep_summary_data(**overrides):
    data = {
        "garmin_sleep_id": 1788117455000,
        "wake_date": date(2026, 8, 31),
        "sleep_start": datetime(2026, 8, 30, 19, 17, 35, tzinfo=timezone.utc),
        "sleep_end": datetime(2026, 8, 31, 2, 43, 31, tzinfo=timezone.utc),
        "total_sleep_seconds": 25316,
        "deep_sleep_seconds": 1980,
        "light_sleep_seconds": 16140,
        "rem_sleep_seconds": 7200,
        "awake_seconds": 1440,
        "awake_count": 1,
        "sleep_score": 79,
        "sleep_score_qualifier": "FAIR",
        "avg_heart_rate": 50,
        "avg_stress": 10.0,
        "avg_respiration": 15.0,
        "avg_spo2": 97.0,
        "lowest_spo2": 86.0,
        "sleep_need_baseline_minutes": 470,
        "sleep_need_actual_minutes": 440,
    }
    data.update(overrides)
    return data


def _hrv_summary_data(**overrides):
    data = {
        "night_date": date(2026, 8, 31),
        "weekly_avg": 76,
        "last_night_avg": 87,
        "last_night_5min_high": 125,
        "baseline_low": 62,
        "baseline_upper": 83,
        "status": "BALANCED",
        "marker_value": 0.544,
    }
    data.update(overrides)
    return data


class UpsertDailyHealthTests(TestCase):
    def test_create_then_update_is_idempotent(self):
        created = upsert_daily_health(_daily_health_data())

        self.assertEqual(DailyHealth.objects.count(), 1)
        self.assertEqual(created.stress_avg, 21)
        self.assertIsNone(created.body_battery_min)
        self.assertEqual(created.body_battery_charged, 0)

        updated = upsert_daily_health(_daily_health_data(
            stress_avg=35,
            body_battery_min=38,
            body_battery_charged=0,
        ))

        self.assertEqual(DailyHealth.objects.count(), 1)
        self.assertEqual(updated.id, created.id)
        self.assertEqual(updated.stress_avg, 35)
        self.assertEqual(updated.body_battery_min, 38)
        self.assertEqual(updated.body_battery_charged, 0)


class UpsertSleepSummaryTests(TestCase):
    def test_create_then_update_by_garmin_id_not_wake_date(self):
        created = upsert_sleep_summary(_sleep_summary_data())

        self.assertEqual(SleepSummary.objects.count(), 1)
        self.assertEqual(created.sleep_score, 79)
        self.assertEqual(created.wake_date, date(2026, 8, 31))

        # Same garmin_sleep_id, changed wake_date + values: must update the
        # same row, not look it up by (and key off) wake_date.
        updated = upsert_sleep_summary(_sleep_summary_data(
            sleep_score=88,
            total_sleep_seconds=26000,
            wake_date=date(2026, 9, 1),
        ))

        self.assertEqual(SleepSummary.objects.count(), 1)
        self.assertEqual(updated.id, created.id)
        self.assertEqual(updated.sleep_score, 88)
        self.assertEqual(updated.total_sleep_seconds, 26000)
        self.assertEqual(updated.wake_date, date(2026, 9, 1))


class UpsertHrvSummaryTests(TestCase):
    def test_create_then_update_is_idempotent(self):
        created = upsert_hrv_summary(_hrv_summary_data())

        self.assertEqual(HrvSummary.objects.count(), 1)
        self.assertEqual(created.last_night_avg, 87)

        updated = upsert_hrv_summary(_hrv_summary_data(last_night_avg=92, status="LOW"))

        self.assertEqual(HrvSummary.objects.count(), 1)
        self.assertEqual(updated.id, created.id)
        self.assertEqual(updated.last_night_avg, 92)
        self.assertEqual(updated.status, "LOW")

    def test_none_input_performs_no_write(self):
        result = upsert_hrv_summary(None)

        self.assertIsNone(result)
        self.assertEqual(HrvSummary.objects.count(), 0)
