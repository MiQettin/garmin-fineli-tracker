import csv
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from activities.models import Activity, ActivityLap
from core.models import DailyLog


class ImportGarminCsvActiveBmrKcalTests(TestCase):
    """Regression tests for active_kcal/bmr_kcal import in import_garmin_csv."""

    def _write_daily_csv(self, rows, fieldnames):
        tmp_dir = Path(tempfile.mkdtemp())
        csv_path = tmp_dir / "garmin_daily.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return csv_path

    def test_active_and_bmr_kcal_are_imported(self):
        csv_path = self._write_daily_csv(
            rows=[{
                "date": "2026-08-01",
                "calories_out": "2500",
                "active_kcal": "700",
                "bmr_kcal": "1800",
            }],
            fieldnames=["date", "calories_out", "active_kcal", "bmr_kcal"],
        )

        call_command("import_garmin_csv", csv=str(csv_path), activities_csv="does-not-exist.csv")

        log = DailyLog.objects.get(date="2026-08-01")
        self.assertEqual(log.calories_out, 2500)
        self.assertEqual(log.active_kcal, 700)
        self.assertEqual(log.bmr_kcal, 1800)

    def test_missing_active_and_bmr_kcal_columns_do_not_crash(self):
        csv_path = self._write_daily_csv(
            rows=[{
                "date": "2026-08-02",
                "calories_out": "2200",
            }],
            fieldnames=["date", "calories_out"],
        )

        call_command("import_garmin_csv", csv=str(csv_path), activities_csv="does-not-exist.csv")

        log = DailyLog.objects.get(date="2026-08-02")
        self.assertEqual(log.calories_out, 2200)
        self.assertIsNone(log.active_kcal)
        self.assertIsNone(log.bmr_kcal)


class RunningAnalyticsViewTests(TestCase):
    """Regression tests for running_analytics_view's overall pace weighting,
    work-lap counting, and relative pace classification. The view requires
    login, so this logs in first."""

    def setUp(self):
        user = get_user_model().objects.create_user(username="analyticstest", password="pw12345")
        self.client.force_login(user)

    def _make_workout(self, activity_id, day, distance_km, lap_time_seconds):
        activity = Activity.objects.create(
            activity_id=activity_id,
            activity_type="running",
            date=day,
            start_time=datetime(day.year, day.month, day.day, 8, 0, tzinfo=timezone.utc),
            name=f"Run {day}",
            duration_minutes=lap_time_seconds / 60,
            distance_km=distance_km,
        )
        ActivityLap.objects.create(
            activity=activity,
            lap_number=1,
            step_type="run",
            lap_time_seconds=lap_time_seconds,
            cumulative_time_seconds=lap_time_seconds,
            distance_km=distance_km,
            avg_pace_min_per_km=(lap_time_seconds / 60) / distance_km,
        )
        return activity

    def test_overall_avg_pace_is_time_distance_weighted_not_averaged(self):
        self._make_workout(1, date(2026, 8, 1), distance_km=2, lap_time_seconds=600)    # 5.0 min/km
        self._make_workout(2, date(2026, 8, 2), distance_km=20, lap_time_seconds=6600)  # 5.5 min/km

        response = self.client.get(reverse("running_analytics"))

        self.assertEqual(response.status_code, 200)
        overall_stats = response.context["overall_stats"]

        # Weighted: (600 + 6600) seconds over (2 + 20) km = 120 min / 22 km.
        expected_weighted = round((7200 / 60) / 22, 2)
        self.assertEqual(overall_stats["avg_pace"], expected_weighted)
        # A plain mean of the two per-workout averages would give 5.25 - that
        # is the bug being fixed, so it must NOT be what is returned.
        self.assertNotEqual(overall_stats["avg_pace"], 5.25)

    def test_total_work_laps_matches_actual_lap_count(self):
        self._make_workout(3, date(2026, 8, 3), distance_km=5, lap_time_seconds=1500)
        self._make_workout(4, date(2026, 8, 4), distance_km=5, lap_time_seconds=1500)

        response = self.client.get(reverse("running_analytics"))

        self.assertEqual(response.context["overall_stats"]["total_intervals"], 2)

    def test_pace_is_shown_without_a_fast_normal_slow_judgement(self):
        # No workout-type-aware target pace exists anywhere in the domain
        # (VK/PK/Pitka are different training purposes with different
        # expected paces), so no fast/normal/slow classification should be
        # generated or rendered - only the factual pace value.
        self._make_workout(5, date(2026, 8, 5), distance_km=10, lap_time_seconds=10 * 60 * 7)

        response = self.client.get(reverse("running_analytics"))

        workout = response.context["workout_data"][0]
        self.assertNotIn("pace_class", workout)
        self.assertIn(workout["avg_pace_formatted"], response.content.decode())
        self.assertNotIn("pace-badge", response.content.decode())
