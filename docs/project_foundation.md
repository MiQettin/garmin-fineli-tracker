# HealthManager Project Foundation

Last updated: 2026-10-12

## Goal

HealthManager is a local-first health tracking app for:

- Energy expenditure from Garmin daily/activity data.
- Energy intake, macros, and foods from the locally imported Fineli database, own products and recipes, manual entries, and Fineli diary CSV imports.
- Daily and weekly review of calorie balance, macros, activity, recovery, and running performance.

The project is designed for later AI/ML analysis while keeping personal data local.

## Core Principles

- Preserve raw exports. Do not manually edit Garmin or Fineli source files.
- Use one canonical daily row as the base unit for analysis.
- Compute derived values in code unless persistence has a clear purpose.
- Unknown is not zero: a value a source doesn't report is stored as empty, never as 0.
- The server is the source of truth: portion grams, nutrient values and recipe values are computed server-side.
- Keep the default deployment local: SQLite, local files, a home server (Raspberry Pi) at most.
- Keep account credentials, Garmin tokens, and personal datasets out of Git.

## Repository Structure

```text
HealthManager/
  backend/
    config/                    Django settings and URL routing
    core/                      Dashboard, settings, DailyLog, UserSettings
      templates/core/
      templatetags/core_extras.py
    activities/                Garmin activities, laps, running analytics
      management/commands/import_garmin_csv.py
      templates/activities/
    nutrition/                 Fineli catalog, food diary, products, recipes, favorites
      management/commands/import_fineli_csv.py
      aggregation.py             Per-user daily/weekly nutrition totals
      recipe_aggregation.py      Recipe nutrient calculation (pure)
      recipe_persistence.py      Recipe recalculation, incl. recipes depending on a changed food
      templates/nutrition/
    health/                    Garmin sleep, HRV, stress, Body Battery; /health/ and /api/health/
    data/                      Generated Garmin CSV files, not committed
    backups/                   backup_db snapshots, not committed
    manage.py
    db.sqlite3                 Local database, not committed
  data/                        Raw/local imports, not committed
  docs/                        Project documentation
  notes/                       Local development notes
  get_garmindata.py            Garmin Connect fetcher (CSV files + health metrics)
  requirements.txt
  .env.example
  .env                         Local credentials, not committed
  .garmintokens/               Garmin auth tokens, not committed
```

## Data Flow

### Garmin

1. `get_garmindata.py` logs in to Garmin Connect.
2. It reuses tokens from `.garmintokens/` when possible.
3. It writes generated CSV files to `backend/data/`:
   - `garmin_daily.csv`
   - `garmin_activities.csv`
   - `garmin_activity_laps.csv`
4. `python manage.py import_garmin_csv` imports those CSV files into SQLite.

Default fetch range is the last 30 completed days. Backfills can be run with:

```bash
python ../get_garmindata.py --start-date 2026-03-17 --end-date 2026-07-21
```

Accepted date formats are `YYYY-MM-DD` and `DD.MM.YYYY`.

### Nutrition

Supported intake sources:

- Fineli foods from the local catalog (required: `import_fineli_csv`).
- Own products (values from the label) and recipes (values calculated from ingredients).
- Manual entries.
- Fineli diary CSV bulk import.

Each `MealEntry` stores the values of its portion at logging time (taken from the database). Saving or deleting one refreshes the shared `DailyLog` cache totals; what users see is computed from their own entries (`nutrition.aggregation.user_daily_totals()`). Recipes recalculate when their ingredients, or the foods those use, change.

### Analysis

Daily Garmin and nutrition data combine around `DailyLog.date`. Running analytics use `Activity` and `ActivityLap` directly.

Future processed/features files can be generated from the database when needed:

- `data/processed/daily_combined.csv`
- `data/features/daily_features.csv`

## Canonical Daily Schema

Core daily fields:

- `date`
- `calories_out`
- `calories_in`
- `protein_g`
- `carbs_g`
- `fat_g`
- `sat_fat_g`
- `steps`
- `distance_meters`
- `moderate_intensity_minutes`
- `vigorous_intensity_minutes`
- `resting_heart_rate`
- `min_heart_rate`
- `max_heart_rate`

Common derived values:

- `balance_kcal = calories_in - calories_out`
- `protein_kcal = protein_g * 4`
- `carbs_kcal = carbs_g * 4`
- `fat_kcal = fat_g * 9`
- `macro_pct = macro_kcal / calories_in` when `calories_in > 0`

## Django App Scope

### Core

`DailyLog`

- One row per date, shared by all users.
- Stores Garmin daily stats and a cache of every user's nutrition totals (never shown as one person's intake).

`UserSettings`

- Singleton user profile and goal settings (preset or custom fitness goal, warning thresholds, Garmin calorie adjustment).
- Calculates BMR, TDEE, target calories, and macro recommendations; `goal_adjustment_kcal()` is the goal value.
- Tracks `last_garmin_sync`.

### Activities

`Activity`

- One row per Garmin activity.
- Stores activity type, date, start time, duration, distance, pace, speed, HR, calories, elevation, cadence, steps, VO2 max, and HR zone seconds.

`ActivityLap`

- One row per activity lap/interval.
- Step types include `warmup`, `run`, `cooldown`, `interval`, `recovery`, `rest`, and `other`.
- Stores timing, distance, pace, speed, HR, elevation, and cadence.

Running analytics:

- Default lap types: `run` and `interval`.
- Optional lap types: `warmup`, `cooldown`.
- Excluded lap types: `recovery`, `rest`, `other`.
- Average pace is weighted by total time divided by total distance.
- Display pace format is `MM:SS.xx`.

### Nutrition

`FineliFood` (+ `FineliFoodNutrient`, `FineliFoodUnit`)

- The Fineli catalog imported from Open Data CSVs: names in three languages, nutrition per 100 g, micronutrients, portion sizes.

`CustomFood` (+ `CustomFoodNutrient`, `CustomFoodIngredient`)

- The user's own products (`kind = product`, per 100 g or 100 ml, optional package) and recipes (`kind = recipe`, built from ingredients, optional servings and finished weight).

`MealEntry` (+ `MealEntryNutrient`), `FavoriteFood`

- Diary entries linked to `DailyLog` and the user; sources `fineli`, `custom`, `manual`, `bulk_import`; meal types breakfast, lunch, dinner, snack, evening snack, or a diary CSV's own meal name.
- Favorites per user.

Details and rules: [database_guide.md](database_guide.md).

## Management Commands

```bash
cd backend

# Import generated Garmin CSV files
python manage.py import_garmin_csv

# Import complete Fineli Open Data CSV folder
python manage.py import_fineli_csv --data-folder "path/to/Fineli_Rel20"

# Consistent database snapshot (backend/backups/)
python manage.py backup_db
```

## Security Rules

- Do not commit `.env`.
- Do not commit `.garmintokens/`.
- Do not commit SQLite databases or generated personal CSV files.
- Prefer VPN/local network if remote access is ever added.
- Do not expose the Django dev server directly to the internet.

## UI Conventions

- All templates extend `core/base.html`.
- Toast notifications use `showToast(message, type, duration)`.
- No browser alerts/prompts/confirms - two-click confirmation (`data-confirm`) for deletes.
- Server-side messages (`django.contrib.messages`) appear as toasts.
- Use filter buttons for analytics and other repeated filter controls.
- Include CSRF tokens in POST requests; async dashboard sync reads `csrftoken` from cookies.

## Documentation Rules

- Update `docs/project_status.md` after each meaningful feature or workflow change.
- Update `docs/database_guide.md` when models or data contracts change.
- Update `docs/ui_guidelines.md` when frontend conventions change.
- Update user guides when routes, commands, or usage steps change.
