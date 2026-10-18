from datetime import date, datetime, timedelta, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import UserSettings
from health.garmin_parser import parse_weigh_ins
from health.garmin_persistence import insert_weigh_ins
from health.garmin_sync import sync_weigh_ins_response
from health.models import WeightEntry
from health.weight import daily_weights, monthly_averages, record_manual_weight, weight_on


def _metric(sample, day, grams, ts=1760000000000):
    return {"samplePk": sample, "calendarDate": day, "weight": grams, "timestampGMT": ts, "sourceType": "INDEX_SCALE"}


def _response(*metrics):
    days = {}
    for m in metrics:
        days.setdefault(m["calendarDate"], []).append(m)
    return {"dailyWeightSummaries": [
        {"summaryDate": d, "allWeightMetrics": ms, "latestWeight": ms[-1]} for d, ms in days.items()
    ]}


class ParseWeighInsTests(TestCase):
    def test_grams_to_kg_and_fields(self):
        entries = parse_weigh_ins(_response(_metric(11, "2026-10-01", 54321.0)))
        self.assertEqual(entries, [{
            "garmin_sample_id": 11, "date": date(2026, 10, 1), "weight_kg": 54.32,
            "measured_at": datetime.fromtimestamp(1760000000, tz=dt_timezone.utc),
        }])

    def test_empty_and_incomplete_responses(self):
        self.assertEqual(parse_weigh_ins(None), [])
        self.assertEqual(parse_weigh_ins({}), [])
        self.assertEqual(parse_weigh_ins(_response({"samplePk": None, "calendarDate": "2026-10-01", "weight": 50000},
                                                   {"samplePk": 2, "calendarDate": "2026-10-01", "weight": None})), [])

    def test_latest_weight_fallback_and_duplicates(self):
        metric = _metric(5, "2026-10-02", 60000.0)
        response = {"dailyWeightSummaries": [{"summaryDate": "2026-10-02", "latestWeight": metric},
                                             {"summaryDate": "2026-10-02", "allWeightMetrics": [metric]}]}
        self.assertEqual([e["garmin_sample_id"] for e in parse_weigh_ins(response)], [5])


class WeightRulesTests(TestCase):
    def _entry(self, day, kg, source=WeightEntry.SOURCE_GARMIN, measured=None, **kw):
        return WeightEntry.objects.create(date=day, weight_kg=kg, source=source, measured_at=measured, **kw)

    def test_manual_wins_over_garmin_on_the_same_day(self):
        d = date(2026, 10, 1)
        self._entry(d, 60.0, measured=datetime(2026, 10, 1, 6, tzinfo=dt_timezone.utc), garmin_sample_id=1)
        self._entry(d, 59.0, source=WeightEntry.SOURCE_MANUAL)
        self.assertEqual(weight_on(d), 59.0)

    def test_latest_garmin_measurement_counts(self):
        d = date(2026, 10, 1)
        self._entry(d, 60.0, measured=datetime(2026, 10, 1, 6, tzinfo=dt_timezone.utc), garmin_sample_id=1)
        self._entry(d, 61.0, measured=datetime(2026, 10, 1, 20, tzinfo=dt_timezone.utc), garmin_sample_id=2)
        self.assertEqual(weight_on(d), 61.0)

    def test_carry_forward_and_ignored(self):
        self._entry(date(2026, 10, 1), 60.0, garmin_sample_id=1)
        self._entry(date(2026, 10, 3), 70.0, garmin_sample_id=2, ignored=True)
        self.assertIsNone(weight_on(date(2026, 9, 30)))
        self.assertEqual(weight_on(date(2026, 10, 5)), 60.0)
        self.assertEqual(daily_weights(date(2026, 9, 30), date(2026, 10, 2)),
                         {date(2026, 10, 1): 60.0, date(2026, 10, 2): 60.0})

    def test_monthly_averages_count_each_day_once(self):
        self._entry(date(2026, 9, 2), 55.0, source=WeightEntry.SOURCE_MANUAL)
        self._entry(date(2026, 9, 20), 56.0, garmin_sample_id=1)
        self._entry(date(2026, 9, 20), 57.0, source=WeightEntry.SOURCE_MANUAL)  # wins on 20.9.
        self._entry(date(2026, 9, 25), 80.0, garmin_sample_id=2, ignored=True)
        self._entry(date(2026, 10, 1), 54.0, garmin_sample_id=3)
        self.assertEqual(monthly_averages(date(2026, 9, 15), date(2026, 10, 31)),
                         [(date(2026, 9, 1), 56.0, 2), (date(2026, 10, 1), 54.0, 1)])

    def test_record_manual_weight_updates_the_day_and_settings(self):
        today = timezone.localdate()
        record_manual_weight(today, 58.04)
        record_manual_weight(today, 57.5)
        self.assertEqual(WeightEntry.objects.filter(date=today).count(), 1)
        self.assertEqual(UserSettings.get_settings().weight_kg, 57.5)


class GarminWeighInSyncTests(TestCase):
    def test_sync_inserts_new_only_and_updates_settings(self):
        today = timezone.localdate()
        response = _response(_metric(1, today.isoformat(), 55000.0))
        self.assertEqual(sync_weigh_ins_response(response), 1)
        self.assertEqual(sync_weigh_ins_response(response), 0)
        self.assertEqual(UserSettings.get_settings().weight_kg, 55.0)

    def test_sync_never_overwrites_corrected_or_removed_entries(self):
        insert_weigh_ins(parse_weigh_ins(_response(_metric(1, "2026-10-01", 55000.0), _metric(2, "2026-10-02", 56000.0))))
        WeightEntry.objects.filter(garmin_sample_id=1).update(weight_kg=50.0, source=WeightEntry.SOURCE_MANUAL)
        WeightEntry.objects.filter(garmin_sample_id=2).update(ignored=True)
        added = insert_weigh_ins(parse_weigh_ins(_response(_metric(1, "2026-10-01", 55000.0), _metric(2, "2026-10-02", 56000.0))))
        self.assertEqual(added, 0)
        self.assertEqual(WeightEntry.objects.get(garmin_sample_id=1).weight_kg, 50.0)
        self.assertTrue(WeightEntry.objects.get(garmin_sample_id=2).ignored)


class WeightPageTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username="weightuser", password="pw12345"))

    def test_page_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("weight_page")).status_code, 302)

    def test_add_weight(self):
        today = timezone.localdate()
        resp = self.client.post(reverse("weight_page"), {"date": today.isoformat(), "weight_kg": "62.4"})
        self.assertRedirects(resp, reverse("weight_page"))
        self.assertEqual(weight_on(today), 62.4)
        self.assertEqual(UserSettings.get_settings().weight_kg, 62.4)

    def test_invalid_weight_is_rejected(self):
        resp = self.client.post(reverse("weight_page"), {"date": "2026-10-01", "weight_kg": "5"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(WeightEntry.objects.exists())

    def test_edit_garmin_entry_becomes_manual(self):
        entry = WeightEntry.objects.create(date=date(2026, 10, 1), weight_kg=55.0, garmin_sample_id=9,
                                           source=WeightEntry.SOURCE_GARMIN)
        self.client.post(reverse("weight_edit", args=[entry.pk]), {"date": "2026-10-01", "weight_kg": "54.0"})
        entry.refresh_from_db()
        self.assertEqual((entry.weight_kg, entry.source, entry.garmin_sample_id), (54.0, "manual", 9))

    def test_delete_manual_removes_and_garmin_is_ignored(self):
        manual = WeightEntry.objects.create(date=date(2026, 10, 1), weight_kg=55.0)
        garmin = WeightEntry.objects.create(date=date(2026, 10, 2), weight_kg=56.0, garmin_sample_id=3,
                                            source=WeightEntry.SOURCE_GARMIN)
        self.client.post(reverse("weight_delete", args=[manual.pk]))
        self.client.post(reverse("weight_delete", args=[garmin.pk]))
        self.assertFalse(WeightEntry.objects.filter(pk=manual.pk).exists())
        garmin.refresh_from_db()
        self.assertTrue(garmin.ignored)
        self.assertNotContains(self.client.get(reverse("weight_page")), "56.0 kg")
        self.assertEqual(self.client.get(reverse("weight_delete", args=[garmin.pk])).status_code, 405)

    def test_page_shows_the_weight_used_and_history(self):
        today = timezone.localdate()
        WeightEntry.objects.create(date=today - timedelta(days=40), weight_kg=60.0)
        WeightEntry.objects.create(date=today, weight_kg=58.5)
        resp = self.client.get(reverse("weight_page"))
        self.assertEqual(resp.context["current"].weight_kg, 58.5)
        self.assertContains(resp, "60.0 kg")
        self.assertNotContains(resp, "weightChart")

    def test_settings_weight_change_is_recorded_as_todays_entry(self):
        data = {
            "weight_kg": "71.2", "height_cm": "170", "age": "40", "sex": "F", "activity_level": "1.55",
            "fitness_goal": "maintain", "custom_goal_kcal": "", "max_daily_deficit": "100",
            "garmin_calorie_adjustment": "0", "min_daily_calories": "1500", "max_daily_calories": "3000",
            "protein_goal_g": "100", "carbs_goal_g": "250", "fat_goal_g": "70", "week_start_day": "0",
            "language": "en", "food_language": "fi",
        }
        self.client.post(reverse("settings"), data)
        self.assertEqual(weight_on(timezone.localdate()), 71.2)

    def test_date_field_is_iso_in_finnish(self):
        UserSettings.objects.update_or_create(pk=1, defaults={"language": "fi"})
        resp = self.client.get(reverse("weight_page"))
        self.assertContains(resp, f'value="{timezone.localdate().isoformat()}"')
