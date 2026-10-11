import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.timezone import now

from core.models import DailyLog
from nutrition.models import MealEntry


class DailyLogRecalculateFromMealsSugarFiberSaltTests(TestCase):
    """Regression test: sugar_g/fiber_g/salt_g must aggregate from MealEntry
    into DailyLog exactly like the existing macro fields do."""

    def test_sugar_fiber_salt_propagate_from_meal_entries(self):
        daily_log = DailyLog.objects.create(date="2026-08-01")
        MealEntry.objects.create(
            daily_log=daily_log, food_name="Oats", calories=300,
            sugar_g=5.0, fiber_g=8.0, salt_g=0.2,
        )
        MealEntry.objects.create(
            daily_log=daily_log, food_name="Banana", calories=100,
            sugar_g=12.0, fiber_g=2.5, salt_g=0.0,
        )

        daily_log.refresh_from_db()

        self.assertEqual(daily_log.sugar_g, 17.0)
        self.assertEqual(daily_log.fiber_g, 10.5)
        self.assertEqual(daily_log.salt_g, 0.2)

    def test_zero_meals_leaves_zero_not_none(self):
        daily_log = DailyLog.objects.create(date="2026-08-02")

        daily_log.recalculate_from_meals()

        self.assertEqual(daily_log.sugar_g, 0)
        self.assertEqual(daily_log.fiber_g, 0)
        self.assertEqual(daily_log.salt_g, 0)


class NavigationDomainGroupingTests(TestCase):
    """Focused regression coverage for the reorganized top-level nav
    (Dashboard / Training / Health & Recovery / Nutrition / Settings) in
    core/templates/core/base.html, checked via the dashboard page. The
    dashboard requires login, so this logs in first."""

    def setUp(self):
        user = get_user_model().objects.create_user(username="navtest", password="pw12345")
        self.client.force_login(user)
        # The dashboard's energy-balance hero section only renders when the
        # current week has at least one day with real data.
        # The window ends yesterday (today is still incomplete).
        DailyLog.objects.create(date=now().date() - timedelta(days=1), calories_in=2000, calories_out=2200)
        self.resp = self.client.get(reverse("week_view"))
        self.content = self.resp.content.decode()
        # Scope nav-structure assertions to the <nav> block itself, since the
        # dashboard body also has its own unrelated quick-link buttons
        # (e.g. a "Food Diary" shortcut) that must not be confused with nav.
        self.nav_html = re.search(r"<nav\b[^>]*>.*?</nav>", self.content, re.S).group(0)
        # Top-level-only view of the nav: dropdown contents stripped out.
        self.nav_top_level = re.sub(r'<ul class="dropdown-menu[^"]*">.*?</ul>', "", self.nav_html, flags=re.S)

    def _is_bare_top_level_link(self, href):
        """True if `href` still appears as a direct <li><a> in the TOP-level
        nav list (the old top-level shape), rather than only inside a
        dropdown (already stripped from nav_top_level)."""
        return re.search(r'<li[^>]*>\s*<a[^>]*href="%s"' % re.escape(href), self.nav_top_level) is not None

    def test_dashboard_link_exists(self):
        self.assertEqual(self.resp.status_code, 200)
        self.assertIn("Dashboard", self.nav_html)

    def test_activities_and_running_grouped_under_training(self):
        self.assertIn("Training", self.nav_html)
        self.assertIn("Activities", self.nav_html)
        self.assertIn("Running", self.nav_html)
        self.assertIn('href="/activities/"', self.nav_html)
        self.assertIn('href="/analytics/running/"', self.nav_html)

    def test_health_page_grouped_under_health_and_recovery(self):
        self.assertIn("Health &amp; Recovery", self.nav_html)
        self.assertIn("Overview", self.nav_html)
        self.assertIn('href="/health/"', self.nav_html)

    def test_diary_and_foods_grouped_under_nutrition(self):
        self.assertIn("Nutrition", self.nav_html)
        self.assertIn("Diary", self.nav_html)
        self.assertIn("Foods", self.nav_html)
        self.assertIn("Recipes", self.nav_html)
        self.assertIn('href="/food-diary/"', self.nav_html)
        self.assertIn('href="/food-diary/custom-foods/"', self.nav_html)

    def test_running_analytics_is_not_top_level(self):
        self.assertNotIn("Run Analytics", self.nav_html)
        self.assertFalse(self._is_bare_top_level_link("/analytics/running/"))

    def test_food_diary_is_not_top_level(self):
        self.assertNotIn("Food Diary", self.nav_html)
        self.assertFalse(self._is_bare_top_level_link("/food-diary/"))

    def test_foods_is_not_top_level(self):
        self.assertFalse(self._is_bare_top_level_link("/food-diary/custom-foods/"))

    def test_existing_routes_still_resolve(self):
        for name in ["week_view", "settings", "activities", "running_analytics", "health_page"]:
            reverse(name)  # raises NoReverseMatch if broken

    def test_dashboard_weekly_energy_balance_preserved(self):
        self.assertContains(self.resp, "Avg daily balance")

    def test_settings_remains_accessible(self):
        resp = self.client.get(reverse("settings"))
        self.assertEqual(resp.status_code, 200)


class DashboardNutritionSummaryTests(TestCase):
    """Focused regression coverage for the Dashboard's new 'Nutrition - Last
    7 days' section: existing energy figures must be untouched, and the new
    macro-energy distribution/coverage must never fabricate zero for an
    unknown MealEntry value or present partial data as an exact percentage.
    All fixtures use the dashboard's default (week_offset=0) window:
    [yesterday - 6, yesterday] - the dashboard's window always ends
    yesterday, excluding today's still-incomplete data. `self.today` here
    names the window's most recent day (yesterday), kept as `_day(0)` for
    every existing test's relative offsets to still line up."""

    def setUp(self):
        user = get_user_model().objects.create_user(username="dashtest", password="pw12345")
        self.client.force_login(user)
        self.today = now().date() - timedelta(days=1)

    def _day(self, offset, **daily_log_kwargs):
        return DailyLog.objects.create(date=self.today - timedelta(days=offset), **daily_log_kwargs)

    def test_existing_energy_intake_expenditure_present(self):
        self._day(0, calories_in=2200, calories_out=2500)
        self._day(1, calories_in=1800, calories_out=2400)
        resp = self.client.get(reverse("week_view"))
        self.assertEqual(resp.context["summary"]["calories_in"], 4000)
        self.assertEqual(resp.context["summary"]["calories_out"], 4900)

    def test_existing_balance_convention_unchanged(self):
        self._day(0, calories_in=2200, calories_out=2500)
        resp = self.client.get(reverse("week_view"))
        # Documented existing convention: balance = intake - expenditure.
        self.assertEqual(resp.context["summary"]["balance_kcal"], 2200 - 2500)
        self.assertEqual(resp.context["energy"]["avg_balance"], -300)

    def test_dashboard_weekly_energy_balance_still_rendered(self):
        self._day(0, calories_in=2200, calories_out=2500)
        resp = self.client.get(reverse("week_view"))
        self.assertContains(resp, "Avg daily balance")

    def test_macro_kcal_uses_4_4_9_factors(self):
        log = self._day(0, calories_in=1, calories_out=1)
        MealEntry.objects.create(
            daily_log=log, food_name="X", meal_type="snack", portion_grams=100,
            protein_g=10, carbs_g=20, fat_g=5,
        )
        resp = self.client.get(reverse("week_view"))
        macros = resp.context["nutrition_summary"]["macros"]
        self.assertAlmostEqual(macros["protein"]["kcal"], 40)
        self.assertAlmostEqual(macros["carbs"]["kcal"], 80)
        self.assertAlmostEqual(macros["fat"]["kcal"], 45)

    def test_macro_percentages_based_on_kcal_not_grams(self):
        log = self._day(0, calories_in=1, calories_out=1)
        MealEntry.objects.create(
            daily_log=log, food_name="X", meal_type="snack", portion_grams=100,
            protein_g=10, carbs_g=20, fat_g=5,
        )
        resp = self.client.get(reverse("week_view"))
        macros = resp.context["nutrition_summary"]["macros"]
        self.assertTrue(resp.context["nutrition_summary"]["percentages_available"])
        # kcal-based: 40/80/45 of 165 total, NOT the gram ratio 10/20/5 of 35.
        self.assertAlmostEqual(macros["protein"]["percent"], 24.2, places=1)
        self.assertAlmostEqual(macros["carbs"]["percent"], 48.5, places=1)
        self.assertAlmostEqual(macros["fat"]["percent"], 27.3, places=1)

    def test_macro_percentages_sum_to_100_when_complete(self):
        log = self._day(0, calories_in=1, calories_out=1)
        MealEntry.objects.create(
            daily_log=log, food_name="X", meal_type="snack", portion_grams=100,
            protein_g=10, carbs_g=20, fat_g=5,
        )
        resp = self.client.get(reverse("week_view"))
        macros = resp.context["nutrition_summary"]["macros"]
        total = macros["protein"]["percent"] + macros["carbs"]["percent"] + macros["fat"]["percent"]
        self.assertAlmostEqual(total, 100.0, places=1)

    def test_average_grams_use_logged_days_not_window_days(self):
        log1 = self._day(0, calories_in=1, calories_out=1)
        log2 = self._day(1, calories_in=1, calories_out=1)
        for log in (log1, log2):
            MealEntry.objects.create(
                daily_log=log, food_name="X", meal_type="snack", portion_grams=100,
                protein_g=10, carbs_g=20, fat_g=5,
            )
        resp = self.client.get(reverse("week_view"))
        ns = resp.context["nutrition_summary"]
        self.assertEqual(ns["logged_days"], 2)
        self.assertEqual(ns["window_days"], 7)
        self.assertEqual(ns["denominator_label"], "Average on logged days")
        self.assertAlmostEqual(ns["macros"]["protein"]["avg_g"], 20 / 2)  # known_sum / logged_days

    def test_day_with_no_meal_entry_is_not_a_zero_intake_day(self):
        self._day(0, calories_in=0, calories_out=0)  # Garmin-only/empty day, no MealEntry at all
        log = self._day(1, calories_in=1, calories_out=1)
        MealEntry.objects.create(
            daily_log=log, food_name="X", meal_type="snack", portion_grams=100, protein_g=10,
        )
        resp = self.client.get(reverse("week_view"))
        self.assertEqual(resp.context["nutrition_summary"]["logged_days"], 1)

    def test_explicit_zero_macro_is_known_zero(self):
        log = self._day(0, calories_in=1, calories_out=1)
        MealEntry.objects.create(
            daily_log=log, food_name="X", meal_type="snack", portion_grams=100,
            protein_g=0, carbs_g=20, fat_g=5,
        )
        resp = self.client.get(reverse("week_view"))
        macros = resp.context["nutrition_summary"]["macros"]
        self.assertEqual(macros["protein"]["coverage"], "complete")
        self.assertEqual(macros["protein"]["avg_g"], 0)

    def test_unknown_macro_is_not_converted_to_zero(self):
        log = self._day(0, calories_in=1, calories_out=1)
        MealEntry.objects.create(
            daily_log=log, food_name="A", meal_type="snack", portion_grams=100,
            protein_g=10, carbs_g=20, fat_g=5,
        )
        MealEntry.objects.create(
            daily_log=log, food_name="B", meal_type="snack", portion_grams=100,
            protein_g=10, carbs_g=20, fat_g=None,  # unknown, not zero
        )
        resp = self.client.get(reverse("week_view"))
        macros = resp.context["nutrition_summary"]["macros"]
        self.assertEqual(macros["fat"]["coverage"], "partial")
        self.assertEqual(macros["fat"]["avg_g"], 5)  # only the known 5g counted, not 5/2

    def test_incomplete_macro_data_does_not_produce_exact_percentages(self):
        log = self._day(0, calories_in=1, calories_out=1)
        MealEntry.objects.create(
            daily_log=log, food_name="A", meal_type="snack", portion_grams=100,
            protein_g=10, carbs_g=20, fat_g=5,
        )
        MealEntry.objects.create(
            daily_log=log, food_name="B", meal_type="snack", portion_grams=100,
            protein_g=10, carbs_g=20, fat_g=None,
        )
        resp = self.client.get(reverse("week_view"))
        ns = resp.context["nutrition_summary"]
        self.assertFalse(ns["percentages_available"])
        self.assertNotIn("percent", ns["macros"]["fat"])

    def test_nutrition_coverage_reports_correct_logged_over_window(self):
        for offset in (0, 2, 5):
            log = self._day(offset, calories_in=1, calories_out=1)
            MealEntry.objects.create(
                daily_log=log, food_name="X", meal_type="snack", portion_grams=100, protein_g=10,
            )
        resp = self.client.get(reverse("week_view"))
        ns = resp.context["nutrition_summary"]
        self.assertEqual(ns["logged_days"], 3)
        self.assertEqual(ns["window_days"], 7)


class PersonalDataAccessControlTests(TestCase):
    """Anonymous visitors must not be able to view personal data or trigger
    state-changing actions anywhere in core/activities/health, or on
    nutrition's write endpoints. Page views redirect to the existing
    Django login flow (settings.LOGIN_URL); fetch()-consumed JSON views
    return a 401 JSON body instead, since a redirect can't be parsed as
    JSON by the calling frontend code."""

    # (url, name) - full-page views protected with @login_required.
    LOGIN_REDIRECT_URLS = [
        ("/", "dashboard/week view"),
        ("/settings/", "settings"),
        ("/activities/", "activities list"),
        ("/analytics/running/", "running analytics"),
        ("/health/", "health/recovery page"),
    ]

    # (url, name) - JSON GET views protected with a manual 401 check.
    JSON_401_GET_URLS = [
        ("/api/health/analytics/", "health analytics API"),
    ]

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="acltest", password="pw12345")

    def test_anonymous_get_protected_pages_redirects_to_login(self):
        for url, name in self.LOGIN_REDIRECT_URLS:
            with self.subTest(view=name):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 302)
                self.assertIn("/admin/login/", resp.url)

    def test_authenticated_get_protected_pages_allowed(self):
        self.client.force_login(self.user)
        for url, name in self.LOGIN_REDIRECT_URLS:
            with self.subTest(view=name):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200)

    def test_anonymous_get_json_endpoints_returns_401(self):
        for url, name in self.JSON_401_GET_URLS:
            with self.subTest(view=name):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 401)

    def test_anonymous_garmin_sync_cannot_be_triggered(self):
        resp = self.client.post("/sync-garmin/")
        self.assertEqual(resp.status_code, 401)

    def test_anonymous_post_write_endpoints_blocked_and_write_nothing(self):
        before = MealEntry.objects.count()

        # Plain form-POST view (redirects on success) -> login redirect.
        resp = self.client.post("/food-diary/add-entry/", {
            "date": "2026-01-01", "food_name": "Anon Food", "meal_type": "snack",
            "portion_grams": "100",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/login/", resp.url)

        # JSON fetch() view -> 401 JSON, not a redirect.
        resp = self.client.post(
            "/food-diary/api/add/",
            data='{"date": "2026-01-01", "name": "Anon Fineli Food", "portion_grams": 100}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

        self.assertEqual(MealEntry.objects.count(), before)
        self.assertFalse(MealEntry.objects.filter(food_name__in=["Anon Food", "Anon Fineli Food"]).exists())

    def test_authenticated_manual_food_entry_still_works_and_is_owned(self):
        self.client.force_login(self.user)
        resp = self.client.post("/food-diary/add-entry/", {
            "date": "2026-01-01", "food_name": "Owned Snack", "meal_type": "snack",
            "portion_grams": "100", "calories": "80",
        })
        self.assertEqual(resp.status_code, 302)  # existing redirect-to-diary behavior preserved
        entry = MealEntry.objects.get(food_name="Owned Snack")
        self.assertEqual(entry.user, self.user)
