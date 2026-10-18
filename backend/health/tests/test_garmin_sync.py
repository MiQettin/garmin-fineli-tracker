from datetime import date, datetime, timezone

from django.test import TestCase

from health.garmin_sync import sync_health_responses
from health.models import DailyHealth, HrvSummary, SleepSummary

REQUESTED_DATE = date(2026, 8, 31)


def _sleep_data(sleep_score=79, total_sleep_seconds=25316, sleep_id=111222333):
    return {
        "dailySleepDTO": {
            "id": sleep_id,
            "calendarDate": "2026-08-31",
            "sleepStartTimestampGMT": 1788117455000,
            "sleepEndTimestampGMT": 1788144211000,
            "sleepTimeSeconds": total_sleep_seconds,
            "deepSleepSeconds": 1980,
            "lightSleepSeconds": 16140,
            "remSleepSeconds": 7200,
            "awakeSleepSeconds": 1440,
            "awakeCount": 1,
            "sleepScores": {"overall": {"value": sleep_score, "qualifierKey": "FAIR"}},
            "avgHeartRate": 50.0,
            "avgSleepStress": 10.0,
            "averageRespirationValue": 15.0,
            "averageSpO2Value": 97.0,
            "lowestSpO2Value": 86,
            "sleepNeed": {"baseline": 470, "actual": 440},
        }
    }


def _hrv_data(last_night_avg=87):
    return {
        "hrvSummary": {
            "calendarDate": "2026-08-31",
            "weeklyAvg": 76,
            "lastNightAvg": last_night_avg,
            "lastNight5MinHigh": 125,
            "baseline": {"balancedLow": 62, "balancedUpper": 83, "markerValue": 0.544},
            "status": "BALANCED",
        }
    }


def _stress_data(avg_stress_level=21):
    return {
        "avgStressLevel": avg_stress_level,
        "maxStressLevel": 89,
        "bodyBatteryValueDescriptorsDTOList": [
            {"bodyBatteryValueDescriptorIndex": 0, "bodyBatteryValueDescriptorKey": "timestamp"},
            {"bodyBatteryValueDescriptorIndex": 1, "bodyBatteryValueDescriptorKey": "bodyBatteryStatus"},
            {"bodyBatteryValueDescriptorIndex": 2, "bodyBatteryValueDescriptorKey": "bodyBatteryLevel"},
        ],
        "bodyBatteryValuesArray": [
            [1000, "MEASURED", 40],
            [2000, "MEASURED", 70],
            [3000, "MEASURED", 55],
        ],
    }


def _body_battery_data(charged=59, drained=81):
    return [{"date": "2026-08-31", "charged": charged, "drained": drained}]


def _respiration_data():
    return {
        "avgWakingRespirationValue": 14.0,
        "lowestRespirationValue": 7.0,
        "highestRespirationValue": 21.0,
    }


def _spo2_data():
    return {
        "averageSpO2": 96.0,
        "lowestSpO2": 86,
        "latestSpO2": 97,
        "latestSpO2TimestampGMT": "2026-08-31T21:00:00.0",
    }


class SyncHealthResponsesTests(TestCase):
    def test_full_orchestration_parses_and_persists_all_three_models(self):
        result = sync_health_responses(
            REQUESTED_DATE,
            _sleep_data(),
            _hrv_data(),
            _stress_data(),
            _body_battery_data(),
            _respiration_data(),
            _spo2_data(),
        )

        self.assertEqual(DailyHealth.objects.count(), 1)
        self.assertEqual(SleepSummary.objects.count(), 1)
        self.assertEqual(HrvSummary.objects.count(), 1)

        # Values below only come out correctly if the raw dicts were run
        # through the parser (not accepted as pre-normalized data).
        daily_health = result["daily_health"]
        self.assertEqual(daily_health.date, REQUESTED_DATE)
        self.assertEqual(daily_health.stress_avg, 21)
        self.assertEqual(daily_health.stress_max, 89)
        self.assertEqual(daily_health.body_battery_start, 40)  # earliest timestamp (1000)
        self.assertEqual(daily_health.body_battery_end, 55)  # latest timestamp (3000)
        self.assertEqual(daily_health.body_battery_min, 40)
        self.assertEqual(daily_health.body_battery_max, 70)
        self.assertEqual(daily_health.body_battery_charged, 59)
        self.assertEqual(daily_health.body_battery_drained, 81)
        self.assertEqual(daily_health.spo2_latest_at, datetime(2026, 8, 31, 21, 0, tzinfo=timezone.utc))

        sleep_summary = result["sleep_summary"]
        self.assertEqual(sleep_summary.garmin_sleep_id, 111222333)
        self.assertEqual(sleep_summary.wake_date, REQUESTED_DATE)
        self.assertEqual(sleep_summary.sleep_score, 79)
        self.assertEqual(sleep_summary.total_sleep_seconds, 25316)

        hrv_summary = result["hrv_summary"]
        self.assertEqual(hrv_summary.night_date, REQUESTED_DATE)
        self.assertEqual(hrv_summary.last_night_avg, 87)

    def test_repeated_orchestration_updates_without_duplicating(self):
        sync_health_responses(
            REQUESTED_DATE,
            _sleep_data(sleep_score=79),
            _hrv_data(last_night_avg=87),
            _stress_data(avg_stress_level=21),
            _body_battery_data(),
            _respiration_data(),
            _spo2_data(),
        )

        result = sync_health_responses(
            REQUESTED_DATE,
            _sleep_data(sleep_score=91, total_sleep_seconds=27000),
            _hrv_data(last_night_avg=95),
            _stress_data(avg_stress_level=40),
            _body_battery_data(charged=70, drained=60),
            _respiration_data(),
            _spo2_data(),
        )

        self.assertEqual(DailyHealth.objects.count(), 1)
        self.assertEqual(SleepSummary.objects.count(), 1)
        self.assertEqual(HrvSummary.objects.count(), 1)

        self.assertEqual(result["daily_health"].stress_avg, 40)
        self.assertEqual(result["daily_health"].body_battery_charged, 70)
        self.assertEqual(result["sleep_summary"].sleep_score, 91)
        self.assertEqual(result["sleep_summary"].total_sleep_seconds, 27000)
        self.assertEqual(result["hrv_summary"].last_night_avg, 95)

    def test_missing_hrv_still_persists_daily_health_and_sleep(self):
        result = sync_health_responses(
            REQUESTED_DATE,
            _sleep_data(),
            None,
            _stress_data(),
            _body_battery_data(),
            _respiration_data(),
            _spo2_data(),
        )

        self.assertEqual(DailyHealth.objects.count(), 1)
        self.assertEqual(SleepSummary.objects.count(), 1)
        self.assertEqual(HrvSummary.objects.count(), 0)
        self.assertIsNone(result["hrv_summary"])

    def test_missing_sleep_does_not_block_other_records(self):
        result = sync_health_responses(
            REQUESTED_DATE,
            None,  # Garmin has no sleep record for this date
            _hrv_data(),
            _stress_data(),
            _body_battery_data(),
            _respiration_data(),
            _spo2_data(),
        )

        self.assertEqual(DailyHealth.objects.count(), 1)
        self.assertEqual(SleepSummary.objects.count(), 0)
        self.assertEqual(HrvSummary.objects.count(), 1)

        self.assertIsNotNone(result["daily_health"])
        self.assertIsNone(result["sleep_summary"])
        self.assertIsNotNone(result["hrv_summary"])

    def test_malformed_nonempty_sleep_still_raises(self):
        with self.assertRaises(KeyError):
            sync_health_responses(
                REQUESTED_DATE,
                {"unexpected": "structure"},
                _hrv_data(),
                _stress_data(),
                _body_battery_data(),
                _respiration_data(),
                _spo2_data(),
            )

        self.assertEqual(SleepSummary.objects.count(), 0)

    def test_missing_body_battery_source_leaves_charge_fields_none(self):
        result = sync_health_responses(
            REQUESTED_DATE,
            _sleep_data(),
            _hrv_data(),
            _stress_data(),
            [],  # no Body Battery daily endpoint data available
            _respiration_data(),
            _spo2_data(),
        )

        daily_health = result["daily_health"]
        self.assertIsNone(daily_health.body_battery_charged)
        self.assertIsNone(daily_health.body_battery_drained)
        # Other DailyHealth sources are unaffected by the missing source.
        self.assertEqual(daily_health.stress_avg, 21)
        self.assertEqual(daily_health.respiration_avg_waking, 14.0)
