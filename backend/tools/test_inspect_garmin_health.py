import unittest

from inspect_garmin_health import sanitize


class SanitizeTests(unittest.TestCase):
    def test_redacts_identifying_fields_and_keeps_health_values(self):
        raw = {
            "userId": 123456,
            "displayName": "someone",
            "sleepScores": {"overall": {"value": 82}},
            "dailySleepDTO": {
                "userProfilePK": 123456,
                "deepSleepSeconds": 5400,
                "lightSleepSeconds": 12000,
            },
            "events": [{"deviceId": 987, "value": 42}],
        }

        result = sanitize(raw)

        self.assertEqual(result["userId"], "***REDACTED***")
        self.assertEqual(result["displayName"], "***REDACTED***")
        self.assertEqual(result["sleepScores"]["overall"]["value"], 82)
        self.assertEqual(result["dailySleepDTO"]["userProfilePK"], "***REDACTED***")
        self.assertEqual(result["dailySleepDTO"]["deepSleepSeconds"], 5400)
        self.assertEqual(result["events"][0]["deviceId"], "***REDACTED***")
        self.assertEqual(result["events"][0]["value"], 42)


if __name__ == "__main__":
    unittest.main()
