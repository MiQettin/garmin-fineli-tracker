# Project Status - HealthManager

Last updated: 2026-10-25

## Current Phase

- Phase: 4 - Nutrition depth (products and recipes, provenance-aware diary import) and Garmin health metrics.
- Status: in daily use. The food diary runs on the owner's Raspberry Pi server (since 2026-10-11); a development machine has a copy of the database.
- Current focus:
  - Ship the latest work to the server - see [deployment.md](deployment.md). The 2026-10-24 release (food search, browsing, micronutrient %) has no migrations; older ones (`core/0006`-`0008`, `nutrition/0018`-`0020`, `health/0002`, `activities/0003`) are applied by `migrate` if still missing.
  - Next feature: training vs nutrition - its own page comparing workouts with carbohydrate and protein intake in g/kg of body weight, with sports-nutrition reference ranges plus the user's own limits; later a summary on the dashboard. The training load model is still open: it will be based on workout duration and level and on upcoming competitions (carb loading), not on heart-rate zones, and its targets will also be shown in the food diary.
  - Publish tested changes to the public `garmin-fineli-tracker` repository.
  - Running analytics improvements.

## What Works Right Now

- **Apps:** `core` (dashboard, settings, `DailyLog`, `UserSettings`, base template, `backup_db`, `/sync-garmin/`), `activities` (activities, laps, running analytics), `nutrition` (Fineli catalog, diary, products, recipes, favorites), `health` (sleep, HRV, stress, Body Battery, respiration, SpO2 and `/api/health/`). Models and rules: [database_guide.md](database_guide.md).
- **Language (English / Suomi):** Settings → Language switches the whole UI - navigation, dashboard, settings, diary, products, recipes, favorites, activities, running analytics and the health page, including JavaScript messages and chart labels. Fineli food names follow the language: the Finnish UI always uses the Finnish names; the English UI lets you choose Fineli's Finnish or English names. Portion unit names follow the UI language. Units are metric only (g, ml, dl, l, km, min/km). Translations live in `backend/locale/fi/LC_MESSAGES/django.po` (the compiled `.mo` is committed); see [ui_guidelines.md](ui_guidelines.md#translations).
- **Dashboard:**
  - The 7-day window ends **yesterday** (today is still incomplete). The Daily Breakdown table shows all 7 days; empty days are left out of the averages. Per-day comparison against the 7-day average.
  - Energy balance against the fitness goal (preset or **custom** kcal/day). The **Avg daily balance** row is colored by how far below the goal the average is: green on track → yellow at the warning threshold → red at twice the threshold (blue for a surplus on cut goals), with the goal and thresholds shown. Thresholds are automatic or a custom value.
  - A manual **Garmin calorie adjustment** (kcal/day) is added to Garmin's expenditure in every calculation (average balance, Daily Breakdown, per-day comparison, training share); the imported Garmin value is not changed.
  - Intake, macros and nutrition coverage count **only the logged-in user's** diary entries.
  - Macro goals, recovery (resting-HR baseline), recent activities, Sync Garmin button in the shared nav.
- **Food diary** ([food_diary_guide.md](food_diary_guide.md)):
  - Products and recipes lists: search, sort, cards or a compact A–Ö list.
  - Food search (local Fineli + own foods; ä/ö/å-safe, all words in any order; Finnish compounds ranked by their last part, English names, synonyms, typo correction, "Show all"), favorites, recent, frequent, manual entry; diary CSV import from the menu.
  - Portion units: Fineli portion sizes, "pakkaus" (product package), ml/dl/l (per-100 ml products), "annos" (recipe servings), grams.
  - Free meals: a meal is added on the page from a text field with the 5 standard meals as suggestions (Breakfast, Lunch, Snack, Dinner, Supper / Aamiainen, Lounas, Välipala, Päivällinen, Illallinen), or any own name; the diary shows only the meals logged that day, in logging order; a whole meal can be deleted (🗑️, two clicks). The `evening_snack` key is shown as Supper / Illallinen (it was Evening snack / Iltapala) - the Fineli diary's "illallinen" was already imported as `evening_snack`. No schema change - `meal_type` was already free text; the model's `choices` stay as they are and only the forms use the free list (`nutrition/meals.py`).
  - Values are taken from the database when logging and frozen on the entry. Entries can be edited (✏️ - values follow grams or portion count) and deleted (two clicks).
  - "Today" follows Finnish time (`TIME_ZONE = 'Europe/Helsinki'`).
- **Products** (Nutrition → Foods): values per 100 g or 100 ml, optional package size. Editing a product recalculates the recipes that use it.
- **Recipes** (Nutrition → Recipes): cards with expandable details, Create, Duplicate, Delete (two clicks; refused while used in another recipe). Two-step ingredient pickers with live Fineli suggestions, Fineli favorites and My Foods search. Optional servings and finished-dish weight. Recalculated automatically when ingredients - or the foods they use - change.
- **Favorites** (Nutrition → Favorites): list, remove, search Fineli and own foods to star.
- **Diary CSV import:** food-level rows with qualifiers and provenance; adds missing rows and updates matching imported rows, never deletes; all files validated first; each day locked during import; meal-slot names kept verbatim.
- **Weight** (Health → Weight): manage the weight history - Garmin weigh-ins stored by the sync and manual entries (add/correct/remove); changing the weight in Settings also records today's entry. Used to scale nutrition targets (g/kg) and for a monthly average (`health.weight.monthly_averages()`) to watch that weight doesn't drop during training - HealthManager is an athlete's app for eating enough for the training, not a weight-loss app. The latest weight updates the weight in Settings; changing the weight in Settings records today's entry.
- **Garmin import:** `get_garmindata.py` (unofficial `garminconnect`, token reuse, date-range backfills) writes daily/activity CSVs for `import_garmin_csv` and writes health metrics and weigh-ins straight to the database (weigh-ins are only read, nothing is written to Garmin). Activity duration prefers Garmin's elapsed `duration`; lap `step_type` comes only from explicit Garmin signals.
- **Running analytics:** `/analytics/running/` with Chart.js trends, name filters and search. Averages count only the workout steps chosen on each activity's page (Intervals tab → Workout steps; default: the runs, without warm-up, recovery and cool-down). Warm-up and Cool-down buttons add those laps of all workouts on top of that choice. Lap step types and lap times come straight from Garmin; pace is total time / distance and heart rate time-weighted, shown as M:SS like Garmin Connect.
- **Access control:** every page requires login (Django admin login, `LOGIN_URL = '/admin/login/'`); logging out returns to `/admin/login/?next=/` so signing in opens the Dashboard. JSON endpoints answer 401 when logged out and 404 for other users' foods.
- **Database operations:** `DATABASE_PATH` for the file location, `backup_db` for consistent snapshots, tests on an in-memory database, read-only health checks ([database_guide.md](database_guide.md#health-checks)).
- **Serving:** gunicorn + whitenoise on the Pi, home network only ([deployment.md](deployment.md)).

## Recent Changes

### 2026-10-24 - Smarter food search

No database changes (`makemigrations --check` reports nothing).

- **Ranking reads Finnish compounds from the end** (`nutrition/food_search.py`, moved out of `views.py`): a name whose first part is a compound ending in the word (*MARJAHILLO*, *KAURAPUURO*) ranks right after an exact match and before one that only starts with it (*HILLOPIIRAKKA*); inflected forms (*MAUSTETTU HILLOLLA*) come last. "hillo" used to show 12 *HILLOPIIRAKKA*s and push the jams past the top 20. The diary ranks each food by its best name among Finnish, Swedish and English, so "jam" finds *MIXED BERRY JAM*.
- **Synonyms** (`SYNONYM_GROUPS`: kana/broileri, hillo/marmeladi, chicken/broiler, jam/marmalade) in every food search; a synonym's match ranks just behind the typed word's.
- **Typo correction:** when the search finds nothing, each word that matches nothing is replaced with the closest Fineli word (`difflib`, whole words then word beginnings; vocabulary cached per process, rebuilt when the foods change). The API returns `corrected_query` and the diary shows "Showing results for …".
- **All matches reachable:** `/food-diary/api/search/` takes `limit` (default 20, max 200) and returns `total`; the diary shows **Show all N results**.
- **Stale results fixed:** the diary search cancels the previous request (`AbortController`), so a slow answer for "hil" can no longer replace the one for "hillo".
- **Own foods in the search:** the diary's search lists up to 5 matching own foods first.
- **Browsing products and recipes:** Nutrition → Foods (Saved products) and Nutrition → Recipes share one browser (`nutrition/static/nutrition/item_browser.js` + `.css`, `includes/item_browser_*.html`): search (recipes also by ingredient name), sort (Name A–Ö in Finnish order, Most used, Recently edited), Cards / compact List (list by default over 12 items), A–Ö letter headings and jump bar. Sort and view are kept per page in `localStorage`. The recipe search used to appear only with more than six recipes.
- **Entry edit page:** no meal field any more (it rendered with a large empty gap, and moving a food is rare); the entry keeps its meal. The server still accepts `meal_type` as before.
- **My products page:** the intro text is behind an ⓘ button; the jump-to-form and Recipes buttons are gone (Recipes is in the menu); **➕ Add product** is a folded `<details>` (opens again when a save fails), so the saved products show first.
- **Micronutrients as % of the daily recommendation:** the diary's day panel shows each vitamin and mineral as a share of the adult recommended (or adequate) intake from the Finnish recommendations 2024 / NNR2023 (`nutrition/reference_intakes.py`, appendices 10 a–d), by the sex and age in Settings; thiamine and niacin per MJ of the day's logged energy; sodium as a share of the 5 g salt maximum. No sex set → no percentages, with a hint to Settings. The table's iron value for women over 70 (7 mg) is as printed in the source.
- **Add Food window:** the tab bar is folded behind a toggle that names the tab in use (search is the default). The Diary CSV tab and the 📥 button next to the date are gone; the import is in the menu (**Nutrition → Import Diary CSV**, `/food-diary/#import-csv` opens the import window).

### 2026-10-18 - Free meals in the food diary

No database changes (`makemigrations --check` reports nothing; no stored values are rewritten).

- **Meals are the user's own.** The diary no longer shows 5 fixed, always-empty sections. A **Meal** field above the meals suggests the standard meals (Breakfast, Lunch, Snack, Dinner, Supper / Aamiainen, Lounas, Välipala, Päivällinen, Illallinen; typing filters them) and accepts any own name (max 50 characters). Picking a suggestion, Enter, or leaving the field adds the meal as an empty section on the page; its **+ Add Food** opens the Add Food window (which no longer has a meal field). A meal is saved with its first food.
- Sections show the meals logged that day, ordered by their first entry (standard meals in day order on a tie, e.g. a CSV-imported day). A stored name that matches a standard meal's label (e.g. an imported `Lounas`) is shown in that meal's section but kept as stored.
- A typed standard name in either language is stored as its key (`Aamiainen`/`Breakfast` -> `breakfast`). The server validates the meal (`nutrition/meals.py`); an empty or too long name is rejected.
- **Delete a whole meal**: 🗑️ on a meal, two clicks like deleting a food (`POST /food-diary/delete-meal/`, only the user's own entries of that day and meal; totals recalculated). An unsaved page-only meal is just removed.
- **Entry editing** uses the same meal field (free text with the standard suggestions); saving without changing the meal keeps the stored value.
- `evening_snack` is now shown as **Supper / Illallinen** (was Evening snack / Iltapala), matching the Fineli diary (its "illallinen" was already imported as `evening_snack`). Entries stored verbatim as `Iltapala` show as their own meal.
- Date navigation: ◀ ▶ arrows without text; on phones the row stays on one line and the CSV import button is the 📥 icon only (with `title`/`aria-label`).
- **Phones:** the Meal field moves below the meals once there are any; a ▾ arrow at each meal's right edge shows or hides its foods (meals start hidden; opened meals stay open per day in `sessionStorage`; **+ Add Food** opens its meal); compact summary cards and meal cards.
- **Desktop:** the diary is at most 900 px wide. Meal headings have no emoji.
- **Day micronutrients:** a **Micronutrients** panel (`<details>`) under the day totals - minerals, vitamins and fats summed from the user's own `MealEntryNutrient` rows, with coverage (*8/11 foods*) when some foods have no value (unknown is not zero). `nutrition.views._day_micronutrients()`.
- **Navigation on phones and tablets:** the top bar shows the main pages as icons (🏠 Dashboard, 🍽️ Diary, 🏃‍♂️ Activities, ❤️ Health); the ☰ menu still lists every page by name. The icons sit at the left edge; the 🏃 HealthManager brand shows only on wide screens (≥992 px).
- The standard meal labels in `nutrition/meals.py` are now marked for translation (`gettext_lazy`), so `update_translations` keeps their Finnish names.

### 2026-10-13

Database changes (approved by the owner):

- `activities/0003` - `ActivityLap.workout_step` and `in_analysis` (per-step choice for running analytics). All 120 runs' laps were re-fetched from Garmin (backup first): lap time is Garmin's lap time and the step type Garmin's own, replacing moving-time laps and guessed step types.

- `health/0002` - `WeightEntry`: weight history (Garmin weigh-ins and manual entries); see [database_guide.md](database_guide.md#health).

- `core/0008` - `UserSettings.language` (`en`/`fi`, default `en`) and `UserSettings.food_language` (`fi`/`en`, default `fi`; used only when the app language is English).

Language support:

- `core.middleware.UserLanguageMiddleware` activates the chosen language for every request and sets the Fineli name language (`nutrition.food_language`).
- Fineli names: `fineli_name()` / `FineliFood.display_name` (English names sentence-cased); search ranks by the shown language; diary entries linked to Fineli show the name in the current language.
- Translation tooling without GNU gettext: `manage.py update_translations` (collects texts from templates, `{% jstrans %}` and Python into the .po) and `manage.py compile_translations` (.po → .mo). Tests fail when a text has no Finnish translation or the .mo is stale.
- Model choices (meal types, activity and lap types, goals) are translated at display time, so they needed no migration.
- Number formatting: Finnish uses decimal commas on the page; numbers placed in inputs, CSS and JavaScript use `|unlocalize`.

### 2026-10-12

Data fixes (approved by the owner, done on the development copy):

- Rolled back a faulty diary CSV re-import and removed 827 duplicate entries that a second account (admin) had imported from the same diary; daily totals recalculated and verified. Snapshots were taken before each step.

Database changes (approved by the owner):

- `core/0007` - `UserSettings.custom_goal_kcal`: a **Custom** fitness goal besides the presets. `goal_adjustment_kcal()` is the single source of the goal for the dashboard, settings and macro recommendations.
- `nutrition/0020` - `CustomFood.kind` (`product`/`recipe`; existing foods classified by whether they have ingredients), `servings` and `cooked_weight_g` (both optional).

Diary and dashboard:

- **Per-user nutrition totals.** `DailyLog` is one row per date shared by all users, and its stored totals summed every account's entries - a second account's import doubled intake from 2026-09-01. The dashboard and diary now compute totals from the user's own entries (`nutrition.aggregation.user_daily_totals()`, `weekly_macro_summary(user, ...)`, `logged_nutrition_days_count(user, ...)`).
- **Diary CSV re-import is safe.** It used to delete and recreate a date's rows; it now adds missing rows, updates matching imported rows and keeps the rest; validates all files before writing; locks each day; stops on pre-existing ambiguous duplicates.
- **Local time.** `TIME_ZONE = 'Europe/Helsinki'` and `timezone.localdate()`: with UTC, food logged between midnight and ~03:00 landed on the previous day.
- **Entry editing** (the edit page was missing): meal, name, grams or portion count; nutrients and micronutrients scale with the amount unless corrected by hand.
- **Values from the database.** Logging takes per-100 g values from `FineliFood`/`CustomFood`, not from the browser's copy; unknown Fineli ids and other users' foods are rejected (no more catalog rows created from requests).
- **Names with an apostrophe** (48 Fineli foods, e.g. MCDONALD'S) could not be selected in the diary - result rows now reference foods by index and escape names.
- **Search:** ä/ö/å match case-insensitively (SQLite's `LIKE` only folds ASCII, so "vehnäjauho" found nothing); every word must match in any order; results ranked by relevance (`name_search()`, `ranked_fineli_search()`).
- Dashboard balance status coloring (green → yellow → red), Daily Breakdown balance includes the Garmin adjustment.
- Login after logout opens the Dashboard instead of the admin index.
- Tests use an explicit in-memory database.

Products and recipes:

- Recipe page for phones: after adding or removing an ingredient the page returns to the same section with the search kept (`_back_to_recipe()`, `?q=`/`?own=` + anchor); Recipe details and the correction form are folded; each ingredient shows its kcal, carbs and protein, with totals; nutrition and other nutrients can be shown per 100 g, per serving or for the whole recipe (switch remembered in the browser).
- Products and recipes are separate (Foods / Recipes pages). Recipe cards, Create, rename, Duplicate, Delete (two clicks).
- Two-step ingredient pickers (list → amount panel) with live Fineli suggestions, Fineli favorites and searchable My Foods.
- Recipes recalculate automatically when ingredients are added or removed, and every recipe using a changed product or recipe is recalculated too (`recalculate_dependent_recipes()`).
- Optional servings ("× annos" in the diary) and finished-dish weight (per-100 g basis).
- Package size is optional for products; package unit follows the nutrition basis; ml/l portions display correctly.
- Deleting a food that another recipe uses is refused (the database would silently drop the ingredient).
- Favorites page; save confirmations shown as toasts (`django.contrib.messages`); two-click delete confirmation (`data-confirm`).
- Fixed crashes: saving a product without a package size, invalid input to the legacy add-product URL, GET on delete URLs.

License: HealthManager's own code moved from MIT to the [PolyForm Noncommercial License 1.0.0](../LICENSE) - commercial use is no longer permitted. Versions already published under MIT keep that license for those copies.

Documentation: rewrote [database_guide.md](database_guide.md) (all models, safe operations, health checks, restore), [food_diary_guide.md](food_diary_guide.md) and [ui_guidelines.md](ui_guidelines.md); [deployment.md](deployment.md) covers replacing the server database with a fresh copy.

### 2026-10-11 (evening) - Fineli portion sizes

- **Database change (approved by owner):** migration `nutrition.0018_fineli_food_units` adds the `FineliFoodUnit` table (food, Fineli unit code, grams). It only creates a table.
- `import_fineli_csv` loads `foodaddunit.csv` (about 20,000 portion sizes for 4,232 foods); the diary's portion picker offers each food's own sizes instead of fixed estimates, and grams are recomputed server-side.
- `nutrition/0019`: custom products get a package size and a per-100 g or per-100 ml basis.
- Fixed: the edit custom food page rendered fields that no longer existed, so saving silently failed.
- Fixed: adding a custom food from the diary sent its id as a Fineli id.
- Fixed: the diary crashed when an entry had an unknown nutrient.
- Fixed: stale CSRF token on logout (403).
- `core/0006`: Garmin calorie adjustment; the custom warning threshold is visible in settings.

### 2026-10-11

- Login required across the whole app.
- **Fixed `import_fineli_csv` storing Fineli `ENERC` (kJ) directly as `energy_kcal`** - every Fineli food showed about 4.2× its real calories. Re-run the import with `--update-existing` on any database imported before this fix. Diary CSV imports were not affected.
- Dashboard window ends yesterday; full 7-day Daily Breakdown; per-day comparison against the 7-day average; Sync Garmin button in the shared nav.
- Mobile layout fixes on food diary, recipe, custom food, activity and health pages.
- Management commands no longer crash with `UnicodeEncodeError` on Windows when output is redirected.
- Added `DATABASE_PATH`, `backup_db`, `STATIC_ROOT` and [deployment.md](deployment.md); `requirements.txt` gained `whitenoise` and `gunicorn` (Linux only).
- Licensing for the public repo: MIT license (replaced by PolyForm Noncommercial on 2026-10-12), [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md), Fineli credit footer, README notice about the personal-use Garmin features. Pinned Chart.js to 4.5.1.

### 2026-09-21

- Fixed bulk diary CSV import rejecting files with meal-slot names outside the original 5 (`Herkku`, `Iltapala`, a custom-renamed slot) - now stored verbatim and rendered as their own diary sections instead of aborting the whole file.
- Fixed bulk diary CSV import performance: switched to `bulk_create()`, cutting a ~400-row import from 2+ minutes to under 1 second.
- Widened `MealEntry.meal_type` (`max_length` 20 -> 50) to fit longer diary meal-slot names (migration `0017_alter_mealentry_meal_type`).
- Food diary page now always shows the 5 standard meal sections plus a dynamic section per non-standard meal name present that day.
- Removed the pace/duration/lap-position heuristic that guessed a Garmin lap's `step_type` (warmup/interval/recovery) when Garmin gave no explicit signal - defaults to `run` instead, matching what Garmin Connect itself shows for an unstructured run.
- Fixed `get_garmindata.py` preferring GPS-derived `movingDuration` over Garmin's elapsed `duration` for an activity's total time - a manual time correction in Garmin Connect (e.g. to match official race timing) was being silently discarded.
- Audited the whole project for gaps; see "Known Issues" below for what came out of it.

### 2026-07-22

- Added run analytics filter buttons for Warm-up and Cool-down laps.
- Added backend URL builders so analytics filters preserve workout type, search text, and lap inclusion state.
- Fixed invalid Django template syntax in analytics hidden inputs.
- Kept Intervals as a lap inclusion filter and made Clear filter visible when any lap filter differs from defaults.
- Updated analytics stats to use weighted pace (`total_time / total_distance`) instead of simple average of lap paces.
- Standardized pace formatting to `MM:SS.xx` in:
  - `activities.models.ActivityLap.pace_formatted()`
  - `activities.views`
  - `core.templatetags.core_extras.group_avg_pace`
  - Chart.js tooltips and axis ticks
- Updated dashboard Garmin sync JavaScript:
  - Reads CSRF token from the `csrftoken` cookie.
  - Calls `showToast(message, type)` with the global toast API signature.
- Updated `sync_garmin` subprocess handling:
  - Uses UTF-8 decoding with replacement for Windows-safe output handling.
  - Lets `get_garmindata.py` own Garmin credential/token validation.
- Updated `get_garmindata.py`:
  - Added `--start-date`, `--end-date`, and `--days`.
  - Reuses Garmin tokens from `.garmintokens/`.
  - Saves tokens after full login.
  - Provides a specific Garmin Cloudflare/HTTP 429 explanation.
  - Exports distance, speed, and pace with higher precision.
- Updated dependency floor to `garminconnect>=0.3.6`.

## Garmin Workflow

### Normal Refresh

```bash
cd backend
python ../get_garmindata.py
python manage.py import_garmin_csv
```

Default fetch range is the last 30 completed days, ending yesterday. Health metrics (sleep/HRV/stress/Body Battery/respiration/SpO2) are written to the database as part of the `get_garmindata.py` run itself - `import_garmin_csv` only covers `garmin_daily.csv` and the activities/laps CSVs.

### Backfill A Date Range

```bash
cd backend
python ../get_garmindata.py --start-date 2026-03-17 --end-date 2026-07-21
python manage.py import_garmin_csv
```

Accepted date formats:
- `YYYY-MM-DD`
- `DD.MM.YYYY`

### Token Behavior

- Tokens are stored in `.garmintokens/`.
- `.garmintokens/` must remain uncommitted because it is account-sensitive.
- If token login fails, the script falls back to credential login and saves fresh tokens.
- If Garmin returns HTTP 429, wait or use a different network for the next full login attempt.

## Current Focus

1. Ship the latest work to the Pi ([deployment.md](deployment.md#3-shipping-code-changes-later)): `backup_db`, `git pull`, `migrate`, `collectstatic`, restart.
2. Running analytics: workout comparison/reference tables, HR zone distribution charts (`hr_zone*_seconds`), date range filtering.
3. Keep `/docs` aligned when views, scripts or data contracts change.

## Future Work

- Athlete reference values for the micronutrient % (iron about 1.7× per the DGE sports nutrition position 2020; sodium without the general salt maximum), switched on in Settings. Needs a new `UserSettings` field and migration - the owner decided on 2026-10-25 not to change the database for now.
- Recipe book (phase 3 of the recipe work, deferred 2026-10-12): preparation instructions and notes, preparation time, tags (breakfast, dessert, gluten-free, ...) for filtering, and later a photo.
- Side-by-side workout comparison.
- Personal records and improvement tracking.
- Training load and recovery balance.

## Decisions And Conventions

- **Database locked (2026-10-11):** no schema changes and no data writes to the database without the owner's explicit approval; the real database is on the Pi.
- **Private repo for development, public for releases:** tested changes are published to `MiQettin/garmin-fineli-tracker` as one release commit per publish; private history is never pushed there.
- SQLite stays local-first; raw exports, account tokens and databases stay out of Git.
- **Unknown is not zero:** a nutrient a source doesn't report is stored as `None`; sums skip it and coverage is reported, never a fabricated 0.
- **Diary entries are snapshots:** values are taken from the database when logged and stored on the entry; later food or recipe changes don't rewrite history.
- **Products vs recipes:** `CustomFood.kind`; adding an ingredient makes a food a recipe.
- **Recipes never stale:** any change to a recipe or to a food it uses recalculates the recipes affected (ingredients first).
- **Per-user display:** `DailyLog`'s stored nutrition totals are a shared all-users cache - never shown as one person's intake; use `user_daily_totals()`.
- **Server is the source of truth:** grams of a portion, nutrient values and recipe values are computed server-side, never taken from the browser.
- **Local time:** "today" is `timezone.localdate()` (Europe/Helsinki).
- `UserSettings` is a singleton (`UserSettings.get_settings()`); the goal value comes from `goal_adjustment_kcal()`.
- Pace is formatted `MM:SS.xx`; group pace is weighted by total time and distance; running analytics excludes recovery/rest/other laps.
- A Garmin lap's `step_type` is only set from an explicit Garmin signal.
- A diary CSV import's own meal-slot name is used verbatim when it isn't one of the 5 standard Finnish names.
- UI: no browser dialogs, toasts for feedback, two-click confirmation, no data inlined in HTML attributes ([ui_guidelines.md](ui_guidelines.md)).

## Known Issues

- **Multi-user is partial.** Only `MealEntry`, `CustomFood` and `FavoriteFood` have a `user`. `DailyLog`, `UserSettings` and all Garmin/activity/health data are shared: a second account shares settings and goals and sees the first user's Garmin data. Diary totals shown are per user. Fine for one person; needs a decision before adding a real second user.
- **Per-100 ml products as recipe ingredients:** the amount is entered in grams while the product's values are per 100 ml, so dense liquids are off by their density (syrup about 1.33).
- **Small issues from the 2026-10-12 code review:** malformed JSON or dates sent to the diary's JSON endpoints give HTTP 500 instead of a 400 message; logging a zero-calorie own food counts it twice in `use_count`; the Foods page's Delete button still uses a browser `confirm()` (and breaks for names with an apostrophe); saving a recipe's final values rounds them to two decimals.
- Activities app has thin test coverage compared to nutrition.
- Home-network deployment only: gunicorn + whitenoise over plain HTTP. Internet exposure needs HTTPS first. `DEBUG` defaults to `True` and `SECRET_KEY` falls back to an insecure default if `.env` is missing - the server's `.env` must set both.
- The full test suite takes about 5-6 minutes on Windows (591 tests).
- Garmin full credential login can trigger Garmin/Cloudflare HTTP 429; token reuse reduces this.
