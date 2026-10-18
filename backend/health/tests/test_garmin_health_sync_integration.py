"""
Focused test for the user-triggered Garmin Health integration in
get_garmindata.py (sync_health_data()). Uses a fake Garmin client at the
client boundary only - no real Garmin login/network calls, no mocking of
the already-tested parser/persistence/orchestration internals.
"""

import sys
from datetime import date
from pathlib import Path

from django.test import TestCase

# get_garmindata.py lives at the repo root, not inside backend/, so it needs
# the repo root on sys.path to be importable from a Django-managed test.
REPO_ROOT = str(Path(__file__).resolve().parents[3])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import get_garmindata  # noqa: E402  (import after the sys.path fix above)

from health.models import DailyHealth, HrvSummary, SleepSummary, WeightEntry

DAY_1 = date(2026, 8, 30)
DAY_2 = date(2026, 8, 31)


def _sleep_response(sleep_id, day):
    return {
        "dailySleepDTO": {
            "id": sleep_id,
            "calendarDate": day.isoformat(),
            "sleepStartTimestampGMT": 1000,
            "sleepEndTimestampGMT": 2000,
            "sleepTimeSeconds": 1000,
        }
    }


def _hrv_response(day, avg):
    return {"hrvSummary": {"calendarDate": day.isoformat(), "lastNightAvg": avg}}


class FakeGarminClient:
    """Stand-in for garminconnect.Garmin exposing only the six Health methods
    and get_weigh_ins."""

    def __init__(self, per_day, weigh_ins=None):
        self.per_day = per_day
        self.weigh_ins = weigh_ins
        self.calls = []

    def get_weigh_ins(self, startdate, enddate):
        self.calls.append(("get_weigh_ins", startdate, enddate))
        return self.weigh_ins

    def get_sleep_data(self, cdate):
        self.calls.append(("get_sleep_data", cdate))
        return self.per_day[cdate]["sleep"]

    def get_hrv_data(self, cdate):
        self.calls.append(("get_hrv_data", cdate))
        return self.per_day[cdate]["hrv"]

    def get_stress_data(self, cdate):
        self.calls.append(("get_stress_data", cdate))
        return self.per_day[cdate]["stress"]

    def get_body_battery(self, startdate, enddate):
        self.calls.append(("get_body_battery", startdate, enddate))
        return self.per_day[startdate]["body_battery"]

    def get_respiration_data(self, cdate):
        self.calls.append(("get_respiration_data", cdate))
        return self.per_day[cdate]["respiration"]

    def get_spo2_data(self, cdate):
        self.calls.append(("get_spo2_data", cdate))
        return self.per_day[cdate]["spo2"]


def _build_client(days_and_markers):
    """days_and_markers: list of (date, distinctive_int_marker)."""
    per_day = {}
    for day, marker in days_and_markers:
        cdate = day.isoformat()
        per_day[cdate] = {
            "sleep": _sleep_response(sleep_id=1000 + marker, day=day),
            "hrv": _hrv_response(day, avg=50 + marker),
            "stress": {"avgStressLevel": marker, "maxStressLevel": marker + 10},
            "body_battery": [{"date": cdate, "charged": marker, "drained": marker}],
            "respiration": {"avgWakingRespirationValue": float(marker)},
            "spo2": {"averageSpO2": float(90 + marker)},
        }
    return FakeGarminClient(per_day)


class SyncHealthDataIntegrationTests(TestCase):
    def test_calls_all_six_health_endpoints_for_requested_date(self):
        client = _build_client([(DAY_2, 1)])

        get_garmindata.sync_health_data(client, DAY_2, DAY_2)

        called_methods = {call[0] for call in client.calls}
        self.assertEqual(
            called_methods,
            {
                "get_sleep_data", "get_hrv_data", "get_stress_data",
                "get_body_battery", "get_respiration_data", "get_spo2_data",
                "get_weigh_ins",
            },
        )

    def test_weigh_ins_are_fetched_once_through_today_and_stored(self):
        client = _build_client([(DAY_1, 1), (DAY_2, 2)])
        client.weigh_ins = {"dailyWeightSummaries": [{"summaryDate": DAY_2.isoformat(), "allWeightMetrics": [
            {"samplePk": 77, "calendarDate": DAY_2.isoformat(), "weight": 61500.0, "timestampGMT": 1760000000000},
        ]}]}

        get_garmindata.sync_health_data(client, DAY_1, DAY_2)

        weigh_in_calls = [call for call in client.calls if call[0] == "get_weigh_ins"]
        self.assertEqual(weigh_in_calls, [("get_weigh_ins", DAY_1.isoformat(), max(DAY_2, date.today()).isoformat())])
        entry = WeightEntry.objects.get(garmin_sample_id=77)
        self.assertEqual((entry.date, entry.weight_kg, entry.source), (DAY_2, 61.5, "garmin"))

    def test_raw_responses_reach_persisted_health_records(self):
        client = _build_client([(DAY_2, 7)])

        get_garmindata.sync_health_data(client, DAY_2, DAY_2)

        daily_health = DailyHealth.objects.get(date=DAY_2)
        self.assertEqual(daily_health.stress_avg, 7)  # distinctive marker proves handoff
        self.assertEqual(daily_health.body_battery_charged, 7)

        sleep_summary = SleepSummary.objects.get(garmin_sleep_id=1007)
        self.assertEqual(sleep_summary.wake_date, DAY_2)

        hrv_summary = HrvSummary.objects.get(night_date=DAY_2)
        self.assertEqual(hrv_summary.last_night_avg, 57)

    def test_health_sync_runs_once_per_date_in_range(self):
        client = _build_client([(DAY_1, 1), (DAY_2, 2)])

        get_garmindata.sync_health_data(client, DAY_1, DAY_2)

        self.assertEqual(DailyHealth.objects.count(), 2)
        sleep_calls = [call for call in client.calls if call[0] == "get_sleep_data"]
        self.assertEqual(len(sleep_calls), 2)
        self.assertEqual({call[1] for call in sleep_calls}, {DAY_1.isoformat(), DAY_2.isoformat()})
