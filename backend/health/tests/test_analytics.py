from datetime import date, datetime, timezone

from django.test import TestCase

from core.models import DailyLog
from health.analytics import get_health_analytics, get_period_start
from health.models import DailyHealth, HrvSummary, SleepSummary

END_DATE = date(2026, 8, 31)


class GetPeriodStartTests(TestCase):
    def test_inclusive_window_boundaries(self):
        self.assertEqual(get_period_start(END_DATE, 7), date(2026, 8, 25))
        self.assertEqual(get_period_start(END_DATE, 28), date(2026, 8, 4))
        self.assertEqual(get_period_start(END_DATE, 90), date(2026, 6, 3))


class HrvAnalyticsTests(TestCase):
    def setUp(self):
        # end_date's own row: null HRV value (proves "current" falls back to
        # a previous date) but a real Garmin baseline/status (proves those
        # are read from "latest record", independent of last_night_avg).
        HrvSummary.objects.create(
            night_date=END_DATE, last_night_avg=None,
            baseline_low=55, baseline_upper=90, status="LOW", marker_value=0.1,
        )
        HrvSummary.objects.create(
            night_date=date(2026, 8, 30), last_night_avg=90,
            baseline_low=60, baseline_upper=85, status="BALANCED", marker_value=0.5,
        )
        HrvSummary.objects.create(night_date=date(2026, 8, 25), last_night_avg=80)   # 7d edge - included
        HrvSummary.objects.create(night_date=date(2026, 8, 24), last_night_avg=40)   # in 28d/90d, outside 7d
        HrvSummary.objects.create(night_date=date(2026, 8, 4), last_night_avg=70)    # 28d edge - included
        HrvSummary.objects.create(night_date=date(2026, 8, 3), last_night_avg=30)    # in 90d, outside 28d
        HrvSummary.objects.create(night_date=date(2026, 6, 3), last_night_avg=60)    # 90d edge - included
        HrvSummary.objects.create(night_date=date(2026, 6, 2), last_night_avg=20)    # one day outside 90d

    def test_current_falls_back_to_latest_non_null(self):
        result = get_health_analytics(END_DATE)["hrv"]

        self.assertEqual(result["current"], 90)
        self.assertEqual(result["current_date"], date(2026, 8, 30))

    def test_window_averages_ignore_out_of_range_and_null_samples(self):
        result = get_health_analytics(END_DATE)["hrv"]

        # 7d: end_date(None), 8/30=90, 8/25=80 -> {90, 80}
        self.assertEqual(result["sample_count_7d"], 2)
        self.assertAlmostEqual(result["avg_7d"], 85.0)

        # 28d adds 8/24=40 and 8/4=70 (still excludes 8/3 and older) -> {90, 80, 40, 70}
        self.assertEqual(result["sample_count_28d"], 4)
        self.assertAlmostEqual(result["avg_28d"], 70.0)

        # 90d adds 8/3=30 and 6/3=60, excludes 6/2 -> {90, 80, 40, 70, 30, 60}
        self.assertEqual(result["sample_count_90d"], 6)
        self.assertAlmostEqual(result["avg_90d"], 370 / 6)

    def test_delta_vs_28d(self):
        result = get_health_analytics(END_DATE)["hrv"]
        self.assertAlmostEqual(result["delta_vs_28d"], 90 - 70.0)

    def test_garmin_baseline_and_status_come_from_latest_record(self):
        result = get_health_analytics(END_DATE)["hrv"]
        # end_date's own row is the latest HrvSummary record even though its
        # last_night_avg is null - baseline/status must still reflect it.
        self.assertEqual(result["baseline_low"], 55)
        self.assertEqual(result["baseline_upper"], 90)
        self.assertEqual(result["status"], "LOW")


class RestingHeartRateAnalyticsTests(TestCase):
    def test_uses_resting_heart_rate_not_min_or_max(self):
        DailyLog.objects.create(date=END_DATE, resting_heart_rate=48, min_heart_rate=40, max_heart_rate=150)
        DailyLog.objects.create(date=date(2026, 8, 30), resting_heart_rate=50, min_heart_rate=41, max_heart_rate=140)

        result = get_health_analytics(END_DATE)["resting_hr"]

        self.assertEqual(result["current"], 48)
        self.assertEqual(result["current_date"], END_DATE)
        self.assertAlmostEqual(result["avg_7d"], 49.0)  # (48 + 50) / 2, not min/max-derived


class SleepAnalyticsTests(TestCase):
    def test_duration_hours_conversion_and_score_trend(self):
        SleepSummary.objects.create(
            garmin_sleep_id=1, wake_date=END_DATE,
            sleep_start=datetime(2026, 8, 30, 22, 0, tzinfo=timezone.utc),
            sleep_end=datetime(2026, 8, 31, 6, 0, tzinfo=timezone.utc),
            total_sleep_seconds=28800, sleep_score=85, avg_heart_rate=52, avg_stress=12.0,
        )
        SleepSummary.objects.create(
            garmin_sleep_id=2, wake_date=date(2026, 8, 30),
            sleep_start=datetime(2026, 8, 29, 22, 0, tzinfo=timezone.utc),
            sleep_end=datetime(2026, 8, 30, 5, 0, tzinfo=timezone.utc),
            total_sleep_seconds=25200, sleep_score=75, avg_heart_rate=54, avg_stress=15.0,
        )

        result = get_health_analytics(END_DATE)["sleep"]

        self.assertEqual(result["duration"]["current"], 28800)
        self.assertAlmostEqual(result["duration"]["current_hours"], 8.0)
        self.assertAlmostEqual(result["duration"]["avg_7d_hours"], (28800 + 25200) / 2 / 3600)
        self.assertEqual(result["sleep_score"]["current"], 85)
        self.assertAlmostEqual(result["sleep_score"]["avg_7d"], 80.0)


class BodyBatteryAnalyticsTests(TestCase):
    def test_start_end_charged_drained_trends(self):
        DailyHealth.objects.create(date=END_DATE, body_battery_start=60, body_battery_end=40, body_battery_charged=55, body_battery_drained=75)
        DailyHealth.objects.create(date=date(2026, 8, 30), body_battery_start=50, body_battery_end=30, body_battery_charged=60, body_battery_drained=80)

        result = get_health_analytics(END_DATE)["body_battery"]

        self.assertEqual(result["start"]["current"], 60)
        self.assertAlmostEqual(result["start"]["avg_7d"], 55.0)
        self.assertEqual(result["end"]["current"], 40)
        self.assertAlmostEqual(result["end"]["avg_7d"], 35.0)
        self.assertEqual(result["charged"]["current"], 55)
        self.assertEqual(result["drained"]["current"], 75)
