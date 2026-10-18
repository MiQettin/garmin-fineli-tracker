import csv
import io
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from activities.models import Activity, ActivityLap
from core.models import DailyLog, UserSettings


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
        expected_weighted = round((7200 / 60) / 22, 4)
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


class GarminLapsTests(TestCase):
    """Lap rows from Garmin's splits: step type straight from intensityType,
    Garmin's lap time (duration, not movingDuration) and the workout step."""

    def _lap(self, index, intensity, step, km, seconds, moving=None, hr=150):
        return {"lapIndex": index, "intensityType": intensity, "wktStepIndex": step, "distance": km * 1000,
                "duration": seconds, "movingDuration": moving or seconds, "averageHR": hr}

    def test_step_type_and_time_come_from_garmin(self):
        from activities.garmin_laps import lap_rows
        laps = [self._lap(1, "WARMUP", 0, 1.0, 344, moving=340), self._lap(2, "ACTIVE", 1, 1.0, 314),
                self._lap(3, "RECOVERY", 2, 0.4, 180), self._lap(4, "ACTIVE", 4, 1.0, 394),
                self._lap(5, "COOLDOWN", 5, 0.5, 200), self._lap(6, None, None, 1.0, 360)]
        rows = lap_rows(7, laps)
        self.assertEqual([r["step_type"] for r in rows], ["warmup", "run", "recovery", "run", "cooldown", "run"])
        self.assertEqual(rows[0]["lap_time_seconds"], 344)  # Garmin's lap time, not moving time
        self.assertEqual([r["workout_step"] for r in rows], [0, 1, 2, 4, 5, None])
        self.assertEqual(rows[-1]["cumulative_time_seconds"], 344 + 314 + 180 + 394 + 200 + 360)


class WorkoutStepAnalysisTests(TestCase):
    """Running analytics counts only the chosen workout steps; by default
    the runs, never warm-up, recovery or cool-down."""

    def setUp(self):
        user = get_user_model().objects.create_user(username="steptest", password="pw12345")
        self.client.force_login(user)
        self.activity = Activity.objects.create(
            activity_id=900, activity_type="running", date=date(2026, 10, 12),
            start_time=datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc), name="PK", duration_minutes=30, distance_km=6)
        # warm-up, a 2-lap PK run (step 1), 2 strides (step 2) with recovery, cool-down
        spec = [("warmup", 0, 1.0, 420, 130), ("run", 1, 1.0, 400, 140), ("run", 1, 1.0, 440, 142),
                ("run", 2, 0.06, 15, 150), ("recovery", 3, 0.2, 100, 140), ("run", 2, 0.06, 15, 152),
                ("cooldown", 5, 0.6, 300, 130)]
        cumulative = 0
        for n, (step_type, step, km, seconds, hr) in enumerate(spec, 1):
            cumulative += seconds
            ActivityLap.objects.create(activity=self.activity, lap_number=n, step_type=step_type, workout_step=step,
                                       lap_time_seconds=seconds, cumulative_time_seconds=cumulative, distance_km=km,
                                       avg_pace_min_per_km=seconds / 60 / km, avg_hr=hr)

    def _workout(self):
        return self.client.get(reverse("running_analytics")).context["workout_data"][0]

    def test_steps_group_laps_of_one_workout_step(self):
        from activities.lap_steps import workout_steps
        steps = workout_steps(list(self.activity.laps.order_by("lap_number")))
        self.assertEqual([(s["step_type"], len(s["laps"])) for s in steps],
                         [("warmup", 1), ("run", 2), ("run", 1), ("recovery", 1), ("run", 1), ("cooldown", 1)])
        self.assertEqual(steps[1]["avg_hr"], round((140 * 400 + 142 * 440) / 840))  # time-weighted

    def test_default_counts_runs_only(self):
        workout = self._workout()
        self.assertEqual(workout["total_time_seconds"], 400 + 440 + 15 + 15)

    def test_selection_leaves_out_the_strides_and_survives(self):
        pk_laps = list(self.activity.laps.filter(workout_step=1).values_list("pk", flat=True))
        resp = self.client.post(reverse("activity_analysis_steps", args=[self.activity.pk]), {"lap": pk_laps})
        self.assertEqual(resp.status_code, 302)
        workout = self._workout()
        self.assertEqual(workout["total_time_seconds"], 840)
        self.assertEqual(workout["total_distance_km"], 2.0)
        self.assertEqual(workout["avg_pace_formatted"], "7:00")
        # Only real choices are stored: the PK run laps stay "default".
        self.assertEqual(set(self.activity.laps.exclude(in_analysis=None).values_list("workout_step", flat=True)), {2})

    def test_reimport_keeps_the_selection(self):
        lap = self.activity.laps.get(lap_number=4)
        lap.in_analysis = False
        lap.save()
        with tempfile.TemporaryDirectory() as tmp:
            daily, acts, laps = (Path(tmp) / n for n in ("d.csv", "a.csv", "l.csv"))
            daily.write_text("date,calories_out\n", encoding="utf-8")
            acts.write_text("activity_id\n", encoding="utf-8")
            laps.write_text("activity_id,lap_number,step_type,lap_time_seconds,cumulative_time_seconds,distance_km,"
                            "avg_pace_min_per_km,avg_speed_kmh,avg_hr,max_hr,total_ascent_m,total_descent_m,avg_cadence,workout_step\n"
                            "900,4,run,16,1000,0.06,4.4,,150,,,,,2\n", encoding="utf-8")
            call_command("import_garmin_csv", csv=str(daily), activities_csv=str(acts), laps_csv=str(laps), stdout=io.StringIO())
        lap.refresh_from_db()
        self.assertEqual((lap.lap_time_seconds, lap.in_analysis), (16, False))

    def test_activity_page_lists_steps_in_finnish(self):
        UserSettings.objects.update_or_create(pk=1, defaults={"language": "fi"})
        resp = self.client.get(reverse("activity_detail", args=[self.activity.pk]))
        self.assertContains(resp, "Treenin vaiheet")
        self.assertContains(resp, 'name="lap"')

    def test_warmup_and_cooldown_toggles_add_those_laps(self):
        url = reverse("running_analytics")
        base = 400 + 440 + 15 + 15
        self.assertEqual(self.client.get(url, {"warmup": "1"}).context["workout_data"][0]["total_time_seconds"], base + 420)
        self.assertEqual(self.client.get(url, {"cooldown": "1"}).context["workout_data"][0]["total_time_seconds"], base + 300)
        resp = self.client.get(url, {"warmup": "1", "cooldown": "1"})
        self.assertEqual(resp.context["workout_data"][0]["total_time_seconds"], base + 720)
        self.assertNotIn("warmup=1", resp.context["lap_filter_urls"]["warmup"])  # the button turns it off again
