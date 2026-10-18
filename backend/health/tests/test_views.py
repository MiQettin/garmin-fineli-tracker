from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import DailyLog
from health.models import DailyHealth, HrvSummary

END_DATE = date(2026, 8, 31)


class HealthAnalyticsViewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="healthtest", password="pw12345")
        self.client.force_login(user)

    def test_valid_request_returns_grouped_analytics(self):
        DailyHealth.objects.create(date=END_DATE, stress_avg=21)
        HrvSummary.objects.create(night_date=END_DATE, last_night_avg=87)

        response = self.client.get(reverse("health_analytics"), {"end_date": "2026-08-31"})

        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["end_date"], "2026-08-31")
        analytics = body["analytics"]
        for group in ("hrv", "resting_hr", "stress", "body_battery", "sleep", "respiration", "spo2", "activity"):
            self.assertIn(group, analytics)

        self.assertEqual(analytics["hrv"]["current"], 87)
        self.assertEqual(analytics["stress"]["current"], 21)

    def test_dates_are_serialized_as_iso_strings(self):
        HrvSummary.objects.create(night_date=END_DATE, last_night_avg=87)

        response = self.client.get(reverse("health_analytics"), {"end_date": "2026-08-31"})

        self.assertEqual(response.status_code, 200)
        current_date = response.json()["analytics"]["hrv"]["current_date"]

        self.assertIsInstance(current_date, str)
        self.assertEqual(current_date, "2026-08-31")

    def test_missing_data_stays_null_not_zero(self):
        # No records created at all for this date.
        response = self.client.get(reverse("health_analytics"), {"end_date": "2026-08-31"})

        self.assertEqual(response.status_code, 200)
        hrv = response.json()["analytics"]["hrv"]

        self.assertIsNone(hrv["current"])
        self.assertIsNone(hrv["avg_7d"])
        self.assertEqual(hrv["sample_count_7d"], 0)

    def test_invalid_end_date_returns_400(self):
        response = self.client.get(reverse("health_analytics"), {"end_date": "not-a-date"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_missing_end_date_param_defaults_and_returns_200(self):
        response = self.client.get(reverse("health_analytics"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("end_date", response.json())

    def test_start_date_scopes_daily_series_to_exact_range(self):
        HrvSummary.objects.create(night_date=date(2026, 8, 29), last_night_avg=70)
        HrvSummary.objects.create(night_date=date(2026, 8, 30), last_night_avg=80)
        HrvSummary.objects.create(night_date=END_DATE, last_night_avg=90)
        HrvSummary.objects.create(night_date=date(2026, 8, 28), last_night_avg=1000)  # outside range
        DailyLog.objects.create(date=date(2026, 8, 30), resting_heart_rate=48)

        response = self.client.get(
            reverse("health_analytics"),
            {"start_date": "2026-08-29", "end_date": "2026-08-31"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["start_date"], "2026-08-29")

        hrv_series = body["analytics"]["hrv"]["daily_series"]
        self.assertEqual(
            hrv_series,
            [
                {"date": "2026-08-29", "value": 70},
                {"date": "2026-08-30", "value": 80},
                {"date": "2026-08-31", "value": 90},
            ],
        )

        rhr_series = body["analytics"]["resting_hr"]["daily_series"]
        self.assertEqual(
            rhr_series,
            [
                {"date": "2026-08-29", "value": None},
                {"date": "2026-08-30", "value": 48},
                {"date": "2026-08-31", "value": None},
            ],
        )

    def test_without_start_date_no_daily_series_is_included(self):
        response = self.client.get(reverse("health_analytics"), {"end_date": "2026-08-31"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIsNone(body["start_date"])
        self.assertNotIn("daily_series", body["analytics"]["hrv"])
        self.assertNotIn("daily_series", body["analytics"]["resting_hr"])

    def test_start_date_after_end_date_returns_400(self):
        response = self.client.get(
            reverse("health_analytics"),
            {"start_date": "2026-09-01", "end_date": "2026-08-31"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_invalid_start_date_returns_400(self):
        response = self.client.get(
            reverse("health_analytics"),
            {"start_date": "not-a-date", "end_date": "2026-08-31"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())
