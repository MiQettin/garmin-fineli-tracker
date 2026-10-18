from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

STATIC_JS_PATH = Path(__file__).resolve().parents[1] / "static" / "health" / "health_page.js"


class HealthPageTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="healthpagetest", password="pw12345")
        self.client.force_login(user)

    def test_page_loads_with_expected_sections(self):
        response = self.client.get(reverse("health_page"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn("Health &amp; Recovery", content)
        for section in ("Overview", "Recovery and health trends", "Body Battery", "Sleep", "Respiration and SpO2", "Daily activity"):
            self.assertIn(section, content)

    def test_navigation_link_present(self):
        response = self.client.get(reverse("health_page"))
        content = response.content.decode()

        self.assertIn('href="/health/"', content)
        self.assertIn("❤️ Health &amp; Recovery", content)

    def test_page_references_analytics_endpoint_not_duplicating_calculations(self):
        response = self.client.get(reverse("health_page"))
        content = response.content.decode()

        self.assertIn('data-analytics-url="/api/health/analytics/"', content)

    def test_from_to_range_controls_and_charts_present(self):
        response = self.client.get(reverse("health_page"))
        content = response.content.decode()

        # From/To are free native date inputs, not fixed 7/28/90-day preset buttons.
        self.assertIn('id="range-from"', content)
        self.assertIn('type="date" id="range-from"', content)
        self.assertIn('id="range-to"', content)
        self.assertIn('type="date" id="range-to"', content)
        self.assertIn('id="range-validation"', content)

        # One shared page-level range feeds both charts.
        self.assertIn('id="hrv-chart"', content)
        self.assertIn('id="rhr-chart"', content)
        self.assertIn("chart.js", content)

    def test_overview_covers_hrv_rhr_sleep_stress_body_battery_steps(self):
        response = self.client.get(reverse("health_page"))
        content = response.content.decode()

        for metric_id in (
            "ov-hrv-value", "ov-rhr-value", "ov-sleep-duration-value",
            "ov-sleep-score-value", "ov-stress-value", "ov-bb-end-value",
            "ov-steps-value",
        ):
            self.assertIn(f'id="{metric_id}"', content)

    def test_missing_range_data_has_an_honest_empty_state(self):
        response = self.client.get(reverse("health_page"))
        content = response.content.decode()

        self.assertIn('id="hrv-chart-empty"', content)
        self.assertIn('id="rhr-chart-empty"', content)


class HealthPageFormattingTests(SimpleTestCase):
    """Source-level check of the shipped formatter (no JS runtime available)."""

    def setUp(self):
        self.js = STATIC_JS_PATH.read_text(encoding="utf-8")

    def test_fmt_rounds_to_whole_number_and_preserves_null_dash(self):
        fmt_block = self.js.split("function fmt(value, unit) {")[1].split("\n  }")[0]

        self.assertIn('if (value === null || value === undefined) return "—";', fmt_block)
        self.assertIn("Math.round(value)", fmt_block)

    def test_fmt_delta_rounds_and_keeps_sign(self):
        delta_block = self.js.split("function fmtDelta(value, unit) {")[1].split("\n  }")[0]

        self.assertIn('if (value === null || value === undefined) return "—";', delta_block)
        self.assertIn("Math.round(value)", delta_block)
        self.assertIn('rounded > 0 ? "+" : ""', delta_block)

    def test_duration_formatter_untouched_by_whole_number_rule(self):
        hours_block = self.js.split("function fmtHours(seconds) {")[1].split("\n  }")[0]

        # fmtHours already converts seconds -> "H h MM min"; this task must not
        # collapse that into a bare rounded number.
        self.assertIn('" h "', hours_block)
        self.assertIn("Math.floor(totalMinutes / 60)", hours_block)

    def test_chart_series_values_stay_unrounded(self):
        chart_block = self.js.split("function buildChart(")[1].split("\n  }")[0]

        self.assertIn("return point.value; }", chart_block)
        self.assertNotIn("Math.round", chart_block)
