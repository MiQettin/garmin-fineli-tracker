# Project Status - HealthManager

Last updated: 2026-10-12

## Current Phase

- Phase: 4 - Nutrition depth (recipes, provenance-aware diary import) and Garmin health metrics
- Dashboard settings include a manual Garmin calorie adjustment. It is added to the displayed daily expenditure without changing the raw imported Garmin value; warning thresholds can be automatic or use the user-entered custom threshold.
- Status: In development; the food diary is in daily use on the owner's Raspberry Pi server (since 2026-10-11). The dev machine's database is a copy.
- Current focus:
  - Develop the public `garmin-fineli-tracker` release: changes are made and tested in the private repo, then published.
  - Keep Garmin CSV regeneration reliable with the current `garminconnect` login flow.
  - Running analytics improvements.

## What Works Right Now

- Django app split into domain apps:
  - `core`: dashboard, week view, settings, `DailyLog`, `UserSettings` (singleton), base templates, shared template tags, the `/sync-garmin/` endpoint.
  - `activities`: activity list/detail pages, laps, running analytics.
  - `nutrition`: food diary, Fineli foods, custom foods and recipes, meal entries, favorites.
  - `health`: Garmin health/recovery metrics - sleep, HRV, stress, Body Battery, respiration, SpO2 - and the `/api/health/` analytics endpoints.
- Dashboard:
  - Weekly energy balance, macro goals, recovery indicators, recent activities.
  - The 7-day window ends **yesterday** (today is still incomplete). The Daily Breakdown table always shows all 7 calendar days, but empty days are left out of the averages. There is a per-day "vs 7-day average" comparison.
  - Garmin sync endpoint at `/sync-garmin/` (`core.views.sync_garmin`, `@require_POST`, login required - anonymous gets a JSON 401). The **Sync Garmin** button and last-sync time are in the shared nav on every page (`core.context_processors.garmin_sync_status`).
  - Sync button uses the browser CSRF cookie and global `showToast(message, type)` API.
- Garmin import - two data paths from one script run:
  - `get_garmindata.py` fetches from Garmin Connect and:
    - Writes `garmin_daily.csv` and `garmin_activities.csv`/`garmin_activity_laps.csv` to `backend/data/`, which then need `python manage.py import_garmin_csv` to land in the DB (`DailyLog`, `Activity`, `ActivityLap`).
    - Also calls `health.garmin_sync.sync_health_responses()` directly per day, which parses and upserts `DailyHealth`, `SleepSummary`, and `HrvSummary` straight into the DB in the same run - no separate import step for health data.
  - Uses `garminconnect>=0.3.6`, stores reusable tokens in `.garmintokens/`, and supports date-range backfills (`--start-date`/`--end-date`/`--days`).
  - `backend/tools/inspect_garmin_health.py` is a manual, read-only discovery utility for capturing raw Garmin health responses to local JSON - not wired into the app.
  - The activity-level `duration_minutes` now prefers Garmin's elapsed `duration` field over `movingDuration`, so a manual time correction made in Garmin Connect (e.g. matching an official race time) is respected instead of silently overridden (fixed 2026-09-21).
- Activities:
  - Activity list and detail views for running and other activity types.
  - Garmin-style lap grouping for warm-up, run, recovery, cool-down, rest, and other laps.
  - A lap's `step_type` is now only ever set from an explicit Garmin signal (structured workout step type, or lap split type/intensity); a lap with no such signal defaults to `run` instead of being guessed from pace/duration/position (fixed 2026-09-21 - the old heuristic mislabeled the first 1-2 laps of any run as "warmup" and short trailing laps as "interval").
  - Pace displays use centisecond precision (`MM:SS.xx`) to avoid rounding drift.
- Run analytics:
  - Dedicated page at `/analytics/running/`.
  - Chart.js trends for pace, heart rate, distance, and lap count.
  - Workout filters: All, VK, PK, Pitka.
  - Lap inclusion filters: Intervals, Warm-up, Cool-down.
  - Defaults include `run` and `interval` laps only; warm-up and cool-down are opt-in.
  - Recovery/rest/other laps remain excluded from analytics pace stats.
  - Average pace is weighted by total lap time divided by total lap distance.
- Health metrics (`health` app):
  - `DailyHealth` (stress, respiration, SpO2, Body Battery), `SleepSummary` (per sleep session), `HrvSummary` (nightly) - all global/unscoped, no `user` field.
  - `health/analytics.py` computes rolling trends/averages for the `/health/` page and `/api/health/` endpoints.
- Food diary:
  - Fineli database search, custom foods, recipes (ingredients from Fineli or other custom foods, recalculated composition), manual entries, favorites, recent/frequent foods, and bulk diary CSV import.
  - `MealEntry`, `CustomFood`, and `FavoriteFood` are scoped to `user` (added since the previous status update); `DailyLog`, `UserSettings`, and all Garmin/health/activity data are **not** user-scoped and remain global/shared regardless of who is logged in.
  - `DailyLog`'s stored nutrition totals (`DailyLog.recalculate_from_meals()`) sum **all** users' `MealEntry` rows for that date, so they are never shown as anyone's intake. The dashboard (intake, balance, macros, Daily Breakdown, nutrition summary) and the food diary's day summary compute each user's own totals from their `MealEntry` rows (`nutrition.aggregation.user_daily_totals()`, `weekly_macro_summary(user, ...)`, `logged_nutrition_days_count(user, ...)`). Fixed 2026-10-12 after a second account's CSV import of the same diary doubled the totals from 2026-09-01.
  - Bulk diary CSV import (`nutrition.views.bulk_import_diary_csv`, POST `/food-diary/api/bulk-import/`, login required):
    - Parses food-level rows (not meal-subtotal or daily-total rows) from a Fineli diary export, including per-nutrient qualifiers (`exact` vs `lt` for a below-detection-limit `<0.1` value) and original source provenance (Fineli food id, amount, unit, classification).
    - The diary's own meal-slot name (e.g. `Aamiainen`, `Herkku`, a custom renamed slot) is used as `MealEntry.meal_type` largely verbatim: the 5 standard Finnish names map onto the app's Breakfast/Lunch/Dinner/Snack/Evening Snack buckets; anything else is stored and displayed as its own section using the CSV's own text, rather than being forced into one of the 5 (fixed 2026-09-21).
    - Deduplicates overlapping uploads and upserts matching CSV-imported rows, preserving rows that are absent from a later file; manual/Fineli/custom entries are untouched.
    - All files are validated before any write (one bad file rejects the whole upload), each diary day is row-locked during import, and pre-existing ambiguous duplicates stop the import without partial changes (fixed 2026-10-12: the old import deleted and recreated a date's rows, so repeated imports could lose data).
    - Uses `bulk_create()` for `MealEntryNutrient` rows - a ~400-row file imports in about a second (fixed 2026-09-21).
  - Fineli catalog import via `python manage.py import_fineli_csv --data-folder ...` - **required** for food search (search is local-only). `ENERC` is converted from kJ to kcal (fixed 2026-10-11).
  - Meal changes still refresh `DailyLog`'s shared (all-users) totals through `DailyLog.recalculate_from_meals()`; displays use per-user totals instead.

- Access control:
  - Every page view in `core`, `activities`, `health` and `nutrition` is `@login_required` and redirects to the Django admin login (`LOGIN_URL = '/admin/login/'`). Logging out returns to `/admin/login/?next=/`, so signing in again opens the Dashboard rather than the admin index; the admin site is only reached by going to `/admin/` directly.
  - JSON endpoints called by `fetch()` (`/sync-garmin/`, `/api/health/...`, `add_fineli_entry`, `bulk_import_diary_csv`, favorites) check authentication themselves and return a JSON 401, because the frontend can't parse a redirect as JSON.
  - New meal entries always get `user=request.user`. Anonymous `user=None` writes are gone.
- Database portability:
  - `DATABASE_PATH` env var overrides the SQLite location (default `backend/db.sqlite3`).
  - `python manage.py backup_db [--output FILE]` writes a consistent, integrity-checked snapshot using SQLite's online backup API, and refuses to run with unapplied migrations.
  - `STATIC_ROOT = backend/staticfiles` for `collectstatic`; whitenoise serves it when `DEBUG=False`, and gunicorn is the server's app server (`requirements.txt`, Linux only).

## Recent Changes

### 2026-10-11 (evening) - Fineli portion sizes

- **Database change (approved by owner):** migration `nutrition.0018_fineli_food_units` adds the `FineliFoodUnit` table (food, Fineli unit code, grams). It only creates a table - existing tables and entries are untouched (the two `MealEntry` help-text changes are SQL no-ops).
- `import_fineli_csv` now loads `foodaddunit.csv` (about 20,000 portion sizes for 4,232 foods).
- Food diary portion picker offers each Fineli food's own sizes (e.g. peeled banana: small 100 g / medium 125 g / large 150 g) instead of fixed estimates; grams are recomputed server-side from the unit. Custom products can be logged as "1 × pakkaus" (their `default_portion_g`, labelled *Package size (g)*) or in grams; recipes in grams only (the old guessed dl/kpl/rkl/tl conversions are gone).
- Fixed: the **edit custom food** page rendered fields that no longer exist on the form, so nutrient values couldn't be edited and saving silently failed. Add and edit forms now share one field template and show validation errors. The logged portion is stored in `MealEntry.source_amount`/`source_unit` (the same fields the diary CSV import already uses) and shown as "1 × iso (kpl) · 150g".
- Fixed: adding a **custom food** from the diary sent its id as a Fineli id, so the entry could be linked to an unrelated Fineli food (wrong micronutrients) or create a bogus Fineli row.
- Fixed: the food diary page crashed (HTTP 500) when an entry had an unknown nutrient - e.g. any of the 51 Fineli foods without saturated fat.
- **To deploy on the Pi:** `backup_db`, `git pull`, `migrate`, then `import_fineli_csv --data-folder <Fineli folder> --update-existing` (the Fineli folder must be copied to the Pi; it is not in Git).

### 2026-10-11

- Login required across the whole app (see "What Works Right Now" → Access control).
- Dashboard window ends yesterday. Full 7-day Daily Breakdown table, per-day comparison against the 7-day average, Sync Garmin button moved into the shared nav.
- Mobile layout fixes on food diary, recipe, custom food, activity and health pages.
- **Fixed `import_fineli_csv` storing Fineli `ENERC` (kJ) directly as `energy_kcal`** - every Fineli food showed about 4.2x its real calories. The value is now divided by 4.184. Re-run the import with `--update-existing` on any database imported before this fix. Diary CSV imports were not affected (they already converted).
- Fixed management commands crashing with `UnicodeEncodeError` on Windows when output is redirected (emoji under cp1252). `manage.py` now forces UTF-8 stdout/stderr.
- Recipe final-value inputs show "Unknown" as placeholder for blank (unknown) values.
- Updated stale nav/health tests to the Bootstrap navbar markup. The full suite passes again (previously 2 failures + 12 errors).
- Added `DATABASE_PATH`, `backup_db`, `STATIC_ROOT` and [deployment.md](deployment.md).
- `requirements.txt`: added `whitenoise` and `gunicorn` (Linux only); removed the unused `fitparse` and the deprecated `dotenv` wrapper package (`python-dotenv` is the real one).
- Licensing for the public repo: MIT [LICENSE](../LICENSE), [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) (libraries, Fineli CC BY 4.0, Garmin trademark/unofficial-API notice), Fineli credit footer on the food diary, custom foods and recipe pages (`nutrition/_fineli_attribution.html`), and a README notice that the Garmin features are for personal use and will be generalized later. Pinned Chart.js to 4.5.1.
- Rewrote [food_diary_guide.md](food_diary_guide.md). The old guide described a live Fineli API search and automatic favorites, neither of which exists.

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

1. Server copy of the database + production serving setup (see [deployment.md](deployment.md)).
2. Running analytics:
   - Add workout comparison/reference tables.
   - Add HR zone distribution charts using `hr_zone*_seconds`.
   - Add date range filtering when needed.
3. Documentation:
   - Keep `/docs` aligned when views, scripts, or data contracts change.

## Future Work

- Side-by-side workout comparison.
- Personal records and improvement tracking.
- Training load and recovery balance.
- Public repository cleanup when ready.

## Decisions And Conventions

- **Database locked (2026-10-11):** no schema changes (models/migrations) and no data writes to the database without the owner's explicit approval - a copy is moving to a server.
- **Private repo for development, public for releases:** all work happens in the private repo. Tested changes are published to the public `MiQettin/garmin-fineli-tracker` as one release commit per publish, on top of the public history - private history is never pushed there.
- SQLite stays local-first.
- Raw exports and account tokens stay out of Git.
- Derived values should be computed in code unless there is a clear reason to persist them.
- Pace should be formatted as `MM:SS.xx` when displaying lap or analytics values.
- Group/average pace should be weighted by total time and total distance.
- Running analytics excludes recovery/rest/other laps by design.
- Toast notifications use `showToast(message, type, duration)`.
- `UserSettings` is treated as a singleton through `UserSettings.get_settings()`.
- `DailyLog.calories_in` and macro totals are recalculated from every user's `MealEntry` rows; never display them as one user's intake - use `user_daily_totals()`.
- A Garmin lap's `step_type` is only ever set from an explicit Garmin signal; never inferred from pace/duration/position.
- A diary CSV import's own meal-slot name is used verbatim when it isn't one of the 5 standard Finnish names, rather than being coerced into one of the 5.

## Known Issues

- **Data model is inconsistently multi-user.** Only `MealEntry`, `CustomFood`, and `FavoriteFood` have a `user` FK. `DailyLog`, `UserSettings` (a singleton), and all of `Activity`/`ActivityLap`/`DailyHealth`/`SleepSummary`/`HrvSummary` are global/shared regardless of who's logged in. Nutrition totals shown in the UI are per user (fixed 2026-10-12), but a second real user still shares settings/goals and sees the first user's Garmin data as if it were their own. Harmless with one person using the app. Needs a deliberate decision before adding a second user: stay single-user (and remove the partial `user` fields) or go fully multi-user (and scope everything).
- Activities app has thin test coverage (~120 lines vs. nutrition's ~3700) despite being where the Garmin step-type heuristic bug lived - the app's most heuristic-heavy code has the least coverage.
- Home-network deployment only: runs on gunicorn + whitenoise over plain HTTP. Internet exposure needs HTTPS first (see [deployment.md](deployment.md)). `DEBUG` defaults to `True` and `SECRET_KEY` falls back to an insecure default if `.env` is missing - the server's `.env` must set both.
- The full test suite takes about 15 minutes on Windows.
- Garmin full credential login can trigger Garmin/Cloudflare HTTP 429; token reuse reduces this.
- Several older docs and comments may still mention historical API exploration scripts that are no longer part of the active workflow.
