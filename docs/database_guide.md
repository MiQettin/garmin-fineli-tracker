# Database Guide - HealthManager

Last updated: 2026-10-12

How the data is stored, the rules the code relies on, and how to inspect, back up, restore and move the database safely.

## Contents

- [Overview](#overview)
- [Working with the database safely](#working-with-the-database-safely)
- [Models](#models) - [core](#core) · [nutrition](#nutrition) · [activities](#activities) · [health](#health)
- [Rules the code relies on](#rules-the-code-relies-on)
- [Common workflows](#common-workflows)
- [Health checks](#health-checks)
- [Migrations](#migrations)
- [Troubleshooting](#troubleshooting)

## Overview

HealthManager uses Django's ORM on a single SQLite file.

| Setting | Value |
| --- | --- |
| Location | `backend/db.sqlite3`, or the path in the `DATABASE_PATH` environment variable (servers keep it outside the code checkout) |
| Tests | Always an in-memory database (`TEST: {'NAME': ':memory:'}`) - running the test suite never touches the real file |
| Time zone | `TIME_ZONE = 'Europe/Helsinki'`, `USE_TZ = True`: datetimes are stored in UTC; "today" is the local (Finnish) date via `timezone.localdate()` |

The database holds four kinds of data, one Django app each:

| App | Models (table) | Holds |
| --- | --- | --- |
| `core` | `UserSettings` (`tracker_usersettings`), `DailyLog` (`tracker_dailylog`) | Profile, goals and settings; one row per day with Garmin daily stats |
| `nutrition` | `FineliFood`, `FineliFoodNutrient`, `FineliFoodUnit`, `CustomFood`, `CustomFoodNutrient`, `CustomFoodIngredient`, `MealEntry`, `MealEntryNutrient`, `FavoriteFood` | The Fineli catalog, your own products and recipes, the food diary |
| `activities` | `Activity` (`tracker_activity`), `ActivityLap` (`tracker_activitylap`) | Garmin activities and laps |
| `health` | `DailyHealth`, `SleepSummary`, `HrvSummary`, `WeightEntry` | Garmin sleep, HRV, stress, Body Battery, respiration, SpO2; weight history |

Older models keep `db_table = "tracker_*"` names from before the app was split; newer ones use the default `<app>_<model>` names.

Users are Django's own `auth_user` table. Only the nutrition data that belongs to a person (`MealEntry`, `CustomFood`, `FavoriteFood`) has a `user`; everything else is shared - see [Per-user data](#per-user-data).

## Working with the database safely

The real food diary lives on the Raspberry Pi server (since 2026-10-11). A development machine has a copy. Treat the server's file as the only one that matters.

**Always:**

1. **Take a snapshot before anything that writes** - updates, migrations, imports, restores:
   ```bash
   cd backend
   python manage.py backup_db                       # -> backend/backups/healthmanager-<timestamp>.sqlite3
   python manage.py backup_db --output my-copy.sqlite3
   ```
   `backup_db` uses SQLite's online backup API (safe while the app is running), checks the snapshot's integrity, and refuses to run while migrations are unapplied.
2. **Inspect with read-only connections.** Open the file with `mode=ro` so a typo cannot change anything:
   ```bash
   cd backend
   python -c "import sqlite3; c = sqlite3.connect('file:db.sqlite3?mode=ro', uri=True); print(c.execute('select count(*) from tracker_mealentry').fetchone())"
   ```
   On the server use the `DATABASE_PATH` file instead of `db.sqlite3`. `python manage.py dbshell` and `manage.py shell` are **not** read-only - avoid them for inspection, or only run queries in them.
3. **Change data through the app**, not by hand: the views keep daily totals, recipe values and diary entries consistent (see [Rules](#rules-the-code-relies-on)). A one-off data fix belongs in a reviewed script or a migration (`RunPython`), run after a snapshot.

**Never:**

- Delete, overwrite or move `db.sqlite3` or the files in `backend/backups/` without a fresh snapshot.
- Copy a development database over the server's database by accident - after the first move the server's copy has newer diary entries. Replacing it on purpose is described in [deployment.md](deployment.md#replacing-the-server-database-with-a-fresh-copy).
- Run `migrate <app> zero`, `squashmigrations`, or edit a migration that has already run on the server.

Snapshots contain everything - hashed passwords, the diary, Garmin and health data. Copy them only over channels you trust.

## Models

Field types follow Django. "null" means the column may be empty; for nutrient values an empty value means **unknown**, never zero (see [Unknown is not zero](#unknown-is-not-zero)).

### core

#### UserSettings

A singleton (always `pk=1`) - read it with `UserSettings.get_settings()`, never create rows by hand. It is shared by all users.

| Fields | Notes |
| --- | --- |
| `weight_kg`, `height_cm`, `age`, `sex`, `activity_level` | Body data for BMR/TDEE (`calculate_bmr()`, `calculate_tdee()`) |
| `fitness_goal` | Preset goal (`aggressive_cut` -500, `moderate_cut` -300, `mild_cut` -200, `maintain` 0, `lean_bulk` +250, `bulk` +400 kcal/day) or `custom` |
| `custom_goal_kcal` (null) | Own daily balance target when `fitness_goal` is `custom` (-2000…+2000) |
| `use_auto_thresholds`, `max_daily_deficit` | Dashboard warning threshold: automatic, or the custom value (the alert level is twice it) |
| `garmin_calorie_adjustment` | kcal/day added to Garmin's expenditure in every calculation; the imported Garmin value itself is never changed |
| `protein_goal_g`, `carbs_goal_g`, `fat_goal_g` | Macro goals |
| `min_daily_calories`, `max_daily_calories`, `week_start_day`, `last_garmin_sync`, `updated_at` | |
| `language` | App language, `en` or `fi` (default `en`) |
| `food_language` | Fineli food name language when `language` is `en` (`fi` by default, or `en`); the Finnish UI always uses Finnish names - see `effective_food_language()` |

`goal_adjustment_kcal()` is the single source of the goal value (preset or custom) - the dashboard, the settings page's target calories and `calculate_recommended_macros()` all use it.

#### DailyLog

One row per date (`date` is unique), **shared by all users**.

| Fields | Filled by |
| --- | --- |
| `calories_out`, `active_kcal`, `bmr_kcal`, `steps`, `distance_meters`, `moderate_intensity_minutes`, `vigorous_intensity_minutes`, `resting_heart_rate`, `min_heart_rate`, `max_heart_rate` | Garmin import (`import_garmin_csv`) |
| `calories_in`, `protein_g`, `carbs_g`, `fat_g`, `sat_fat_g`, `sugar_g`, `fiber_g`, `salt_g` | `recalculate_from_meals()` - the sum of **every user's** `MealEntry` rows that day |

The stored nutrition totals are a shared cache. **Never show them as one person's intake** - use `nutrition.aggregation.user_daily_totals(user, start, end)`, which sums only that user's entries (the dashboard and the diary do). `balance_kcal` (`calories_in - calories_out`) has the same limitation and does not include the Garmin adjustment.

### nutrition

```text
FineliFood ─┬─< FineliFoodNutrient         (micronutrients per 100 g)
            ├─< FineliFoodUnit             (portion sizes: "iso (kpl)" = 150 g)
            ├─< CustomFoodIngredient >─┐   (recipe ingredient: Fineli food ...)
            └─< FavoriteFood            │
CustomFood ─┬─< CustomFoodNutrient      │
            ├─< CustomFoodIngredient ───┘   (... or another CustomFood: source_custom_food)
            └─< FavoriteFood
DailyLog ───< MealEntry ─< MealEntryNutrient
              MealEntry >── FineliFood / CustomFood   (optional links, SET_NULL)
```

#### FineliFood

The Fineli catalog, imported with `import_fineli_csv` (about 4,200 foods). Values are per 100 g.

| Fields | Notes |
| --- | --- |
| `fineli_id` (unique), `name_fi`, `name_sv`, `name_en` | Names are upper case (`JAUHO, VEHNÄJAUHO, PUOLIKARKEA`) |
| `energy_kcal`, `protein_g`, `carbs_g`, `sugar_g`, `fat_g`, `sat_fat_g`, `fiber_g`, `salt_g` (null) | Energy converted from kJ, salt from mg |
| `usage_count`, `last_used` | Diary ranking ("most used") |

Only `import_fineli_csv` creates catalog rows. Logging an unknown Fineli id is rejected (it used to create a row from the browser's data).

#### FineliFoodNutrient, CustomFoodNutrient, MealEntryNutrient

Micronutrients (sodium, vitamins, ...) as rows: `nutrient_key`, `value`, `unit`, `qualifier` (`exact`, or `lt` for Fineli's below-detection "<0.1"). One row per key per food or entry (unique constraint). A missing row means unknown.

#### FineliFoodUnit

Fineli portion sizes: `fineli_food`, `unit_code` (`KPL_S/M/L` pieni/keskikokoinen/iso kpl, `PORTS/M/L` annos, `DL`, `RKL`, `TL`, `KPL_VALM`), `mass_g`. Unique per food and unit. The server converts "2 × iso (kpl)" to grams from this table, never from the browser.

#### CustomFood

The user's own products **and** recipes (`user` - owner).

| Fields | Notes |
| --- | --- |
| `kind` | `product` (values typed from the label) or `recipe` (values calculated from ingredients). Adding an ingredient makes a food a recipe. **Foods** lists products, **Recipes** lists recipes |
| `name`, `brand` | |
| `energy_kcal_per_100g` … `salt_per_100g` (null) | Per 100 g - or per 100 ml when `nutrition_basis` is `ML`. For recipes, written by recalculation (or corrected by hand on the recipe page) |
| `nutrition_basis` | `G` (per 100 g) or `ML` (per 100 ml) |
| `package_amount` (null), `package_unit` | Optional package size (`G`, or `ML`/`DL`/`L` for per-100 ml products). When set, the diary offers "1 × pakkaus" |
| `default_portion_g` | Package size in grams/ml, kept in sync with `package_amount`; legacy |
| `servings` (null) | Recipes: how many servings it makes. When set, the diary offers "1 × annos" = yield / servings |
| `cooked_weight_g` (null) | Recipes: weight of the finished dish. Per-100 g values are based on it; otherwise on the ingredients' total mass (`recipe_aggregation.recipe_yield_grams()`) |
| `use_count`, `created_at`, `updated_at` | |

#### CustomFoodIngredient

One ingredient row of a recipe: `custom_food` (the recipe), exactly one of `fineli_food` or `source_custom_food` (another product or recipe of the same user), and `amount_grams` (> 0). Database constraints enforce "exactly one source" and the positive amount; `clean()` also rejects other users' foods and circular recipes.

#### MealEntry

One food in the diary.

| Fields | Notes |
| --- | --- |
| `user`, `daily_log` | Owner and day |
| `meal_type` | `breakfast`, `lunch`, `dinner`, `snack`, `evening_snack`, or a free meal name (max 50 characters): the user's own (e.g. `Ennen kisaa`) or a diary CSV's verbatim (e.g. `Herkku`). The model's `choices` list only the 5 standard keys; the forms validate free names themselves (`nutrition/meals.py`) |
| `source_type` | `fineli`, `custom`, `manual`, `bulk_import` |
| `fineli_food`, `custom_food` (null) | Optional link to the source; set to empty if the source is deleted |
| `food_name`, `portion_grams` | `portion_grams` is ml for per-100 ml products |
| `source_amount`, `source_unit` | The logged portion: `2` × `KPL_L`, `1` × `PKG` (pakkaus), `1` × `SERV` (annos), `ML`/`DL`/`L`; shown as "2 × iso (kpl) · 300g" |
| `calories`, `protein_g`, … `salt_g` (null) | **Values for the whole portion**, frozen when logged |
| `*_qualifier` | `exact` / `lt` per macro (diary CSV imports) |
| `source_food_id`, `source_food_use_classification`, `source_ingredients_classification` | Diary CSV provenance |
| `notes`, `created_at` | |

#### FavoriteFood

`user` plus exactly one of `fineli_food` / `custom_food`; unique per user and food.

### activities

- **Activity** - one Garmin activity: `activity_id` (unique), `activity_type`, `date`, `start_time`, `name`, `duration_minutes`, `distance_km`, `pace_min_per_km`, `avg_speed_kmh`, `avg_hr`, `max_hr`, `calories`, elevation, `avg_cadence`, `steps`, `vo2max`, `hr_zone1_seconds` … `hr_zone5_seconds`.
- **ActivityLap** - one lap: `activity`, `lap_number` (unique together), `step_type` (straight from Garmin's lap `intensityType`: WARMUP→`warmup`, ACTIVE→`run`, RECOVERY→`recovery`, REST→`rest`, COOLDOWN→`cooldown`, INTERVAL→`interval`; laps without one are `run`), `workout_step` (Garmin `wktStepIndex`), lap time = Garmin's `duration` (laps add up to the activity time), distance, pace, HR, ascent/descent, cadence, and `in_analysis` - the user's per-step choice for running analytics (empty = default: `run` and `interval` laps). Imports never set `in_analysis`, and a re-import removes laps beyond the new last lap.

Pace is stored as decimal minutes per km and shown as `MM:SS.xx`; group averages are total time / total distance.

### health

Written directly by `get_garmindata.py` (no CSV step), shared by all users:

- **DailyHealth** - per `date` (unique): stress, respiration, SpO2, Body Battery.
- **SleepSummary** - per Garmin sleep session (`garmin_sleep_id` unique): `wake_date`, stages, score, averages.
- **HrvSummary** - per `night_date` (unique): weekly/nightly averages, baseline, status.
- **WeightEntry** - weight history: Garmin weigh-ins (`source='garmin'`, `garmin_sample_id` unique) and entries made on the Weight page or by changing the weight in Settings (`source='manual'`). The sync only inserts new Garmin samples and never changes an existing row: a corrected Garmin entry becomes `manual` and keeps its sample id; a removed Garmin entry is kept with `ignored=True` so it is not imported again. The weight of a day comes from `health.weight.weight_on()` (manual wins on the same date, otherwise the latest measurement; days without one use the most recent earlier entry). The latest weight is copied into `UserSettings.weight_kg` (`sync_settings_weight()`).

## Rules the code relies on

### Unknown is not zero

A nutrient value that a source does not report is stored as empty (`None`), never as `0`. `0` means the source says zero. Sums skip unknown values; the dashboard's macro coverage reports "partial" instead of pretending. Keep this when writing code or data fixes.

### Diary entries are snapshots

When a food is logged, the server takes the per-100 g values **from the database** (`FineliFood` or `CustomFood`, never the browser's copy), scales them to the portion and stores them on the `MealEntry` (and its `MealEntryNutrient` rows). Later changes to the food or recipe do **not** change entries already logged. Editing an entry's grams or portion count scales its stored values (and micronutrients) proportionally unless they are corrected by hand. Deleting a food or recipe keeps its diary entries (the link becomes empty).

### Daily totals

Saving or deleting a `MealEntry` calls `DailyLog.recalculate_from_meals()`, which refreshes the shared cache totals. What a user sees is always computed from their own entries (`user_daily_totals()`, `weekly_macro_summary(user, ...)`, `logged_nutrition_days_count(user, ...)`).

### Recipes are recalculated, never stale

- Adding or removing an ingredient, changing the finished-dish weight, or pressing **Recalculate recipe** recalculates the recipe (`recalculate_custom_food_recipe_with_dependencies()`: its nested recipes first).
- When a food changes - a product edited, a recipe recalculated, final values saved by hand - every recipe that uses it, also through other recipes, is recalculated too (`recalculate_dependent_recipes()`: ingredients before the recipes using them).
- A value is only written when every ingredient knows it; otherwise it is stored as unknown.
- Values corrected by hand on the recipe page stay until the recipe's ingredients (or their values) change.

### Deleting foods

`CustomFoodIngredient.source_custom_food` cascades in the database: deleting a food would silently remove it from every recipe that uses it. The app therefore **refuses to delete a product or recipe that is an ingredient of another recipe** and names those recipes. Deleting through the Django admin or a shell bypasses this check - don't.

### Per-user data

| Per user | Shared by everyone |
| --- | --- |
| `MealEntry`, `CustomFood` (products and recipes), `FavoriteFood` | `DailyLog`, `UserSettings`, Fineli catalog, all activity and health data |

Views always filter by `request.user`; other users' foods and entries return 404. With more than one account, settings, goals and Garmin data are shared - see "Known Issues" in [project_status.md](project_status.md).

### Constraints at a glance

| Table | Constraint |
| --- | --- |
| `DailyLog`, `DailyHealth` | `date` unique |
| `HrvSummary` | `night_date` unique |
| `WeightEntry` | `garmin_sample_id` unique (empty for manual entries) |
| `FineliFood` / `Activity` / `SleepSummary` | `fineli_id` / `activity_id` / `garmin_sleep_id` unique |
| `ActivityLap` | (`activity`, `lap_number`) unique |
| `*Nutrient` tables | (owner, `nutrient_key`) unique |
| `FineliFoodUnit` | (`fineli_food`, `unit_code`) unique |
| `CustomFoodIngredient` | exactly one source; `amount_grams > 0` |
| `FavoriteFood` | exactly one food; unique per user and food |

## Common workflows

### Back up and restore

```bash
cd backend
python manage.py backup_db
```

To restore a snapshot: stop the app, copy the snapshot over the database file (`DATABASE_PATH` on the server), delete any `-journal`/`-wal` file next to it, start the app, then run `python manage.py migrate` (a no-op if the snapshot is current). Take a snapshot of the current file first if it might still be needed.

### Move or replace the server database

See [deployment.md](deployment.md): first move, shipping code updates, and replacing the server's database with a fresh copy on purpose.

### Import data

```bash
cd backend
python manage.py import_fineli_csv --data-folder "path/to/Fineli_Rel20"                     # Fineli catalog (required for search)
python manage.py import_fineli_csv --data-folder "path/to/Fineli_Rel20" --update-existing   # refresh after a new release
python ../get_garmindata.py                                                                  # Garmin -> CSVs + health data
python manage.py import_garmin_csv                                                           # Garmin CSVs -> DailyLog, activities
```

Diary CSV exports are imported from the menu (Nutrition → 📥 Import Diary CSV, which opens the import window on the diary page). The import only adds missing rows and updates matching imported rows; it never deletes. See [food_diary_guide.md](food_diary_guide.md#diary-csv-import).

### Refresh stale daily totals

The cache in `DailyLog` should never drift, but if a health check reports a mismatch, recalculate (after a snapshot):

```bash
cd backend
python manage.py shell -c "from core.models import DailyLog; [d.recalculate_from_meals() for d in DailyLog.objects.all()]"
```

### Recalculate a recipe

Press **Recalculate recipe** on its page - it also updates the recipes that use it. In code: `recalculate_custom_food_recipe_with_dependencies(recipe)` followed by `recalculate_dependent_recipes(recipe)` from `nutrition.recipe_persistence`.

### Per-user totals in code

```python
from nutrition.aggregation import user_daily_totals
totals = user_daily_totals(user, start_date, end_date)   # {date: {"calories_in": ..., "protein_g": ...}}
```

## Health checks

Read-only checks to run after an import, a restore or a move. Run from `backend/` (use the `DATABASE_PATH` file on the server). Every line should print 0 except the integrity check (`ok`).

```bash
python - <<'EOF'
import sqlite3
c = sqlite3.connect("file:db.sqlite3?mode=ro", uri=True)
checks = {
    "integrity (expect ok)": "pragma integrity_check",
    "foreign key violations": "select count(*) from pragma_foreign_key_check",
    "daily totals out of sync": """select count(*) from tracker_dailylog d where calories_in !=
        coalesce((select cast(sum(calories) as int) from tracker_mealentry m where m.daily_log_id = d.id), 0)""",
    "diary entries without owner": "select count(*) from tracker_mealentry where user_id is null",
    "duplicate diary rows": """select count(*) from (select 1 from tracker_mealentry group by user_id, daily_log_id,
        meal_type, food_name, source_type, source_food_id, source_amount, source_unit, portion_grams, calories
        having count(*) > 1)""",
    "products with ingredients": """select count(*) from tracker_customfood f where kind = 'product'
        and exists(select 1 from nutrition_customfoodingredient i where i.custom_food_id = f.id)""",
    "ingredients from another user": """select count(*) from nutrition_customfoodingredient i
        join tracker_customfood p on p.id = i.custom_food_id join tracker_customfood s on s.id = i.source_custom_food_id
        where p.user_id is not s.user_id""",
}
for name, sql in checks.items():
    print(f"{name}: {c.execute(sql).fetchone()[0]}")
EOF
```

On Windows PowerShell, save the script to a file and run it with `python file.py`. A "duplicate diary rows" count can be legitimate if you really logged the same food twice in the same meal.

## Migrations

Applied migrations, newest last per app:

| App | Latest | Recent schema changes |
| --- | --- | --- |
| `core` | `0008_usersettings_language` | `0006` Garmin calorie adjustment, `0007` custom fitness goal, `0008` app language and Fineli name language |
| `nutrition` | `0020_customfood_kind_servings_cooked_weight` | `0018` Fineli portion sizes, `0019` package size and per-100 ml basis, `0020` product/recipe kind, servings, finished weight (also classified existing foods) |
| `activities` | `0003_lap_workout_step_in_analysis` | `0002` HR zone seconds, `0003` lap workout step and analysis choice |
| `health` | `0002_weightentry` | `0002` weight history |

Rules (they keep the server upgradeable with a plain `migrate`):

- Every model change ships with its migration, committed with the code. On the server: `backup_db`, `git pull`, `migrate`.
- Changes to existing data happen inside the migration (`RunPython`), never by hand on the server.
- Never edit, delete, squash or reset a migration that has run on the server.
- Before pushing: `python manage.py makemigrations --check --dry-run` must print "No changes detected".
- In this repository, schema changes and data writes need the owner's explicit approval (see `AGENTS.md`).

## Troubleshooting

**Food search finds nothing.** The Fineli catalog is empty - run `import_fineli_csv`.

**Fineli calories are about 4× too high.** The catalog was imported before the kJ→kcal fix (2026-10-11): re-run `import_fineli_csv --update-existing`. Logged entries keep their values.

**A recipe's values look wrong.** Press **Recalculate recipe**. Values that stay empty mean an ingredient doesn't report that nutrient. Check that the finished-dish weight (if set) is the weight of the whole dish.

**Daily totals or the dashboard look doubled.** Totals shown to a user only count that user's entries; if the shared `DailyLog` cache drifts, see [Refresh stale daily totals](#refresh-stale-daily-totals). Run the [health checks](#health-checks).

**Food logged after midnight landed on the previous day.** Fixed 2026-10-12 (local time zone). An entry logged on the wrong day before that can't be moved by editing - delete it and add it again on the right date.

**`backup_db` refuses to run.** Migrations are unapplied - run `python manage.py migrate` first (after making sure the code matches the database).

**Garmin CSV files are missing.** Run `python ../get_garmindata.py`, then `python manage.py import_garmin_csv`. Re-running imports updates rows (`update_or_create`); it does not duplicate them.
