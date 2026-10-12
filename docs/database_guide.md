# Database Guide - HealthManager

Last updated: 2026-07-22

## Overview

HealthManager uses Django ORM with SQLite. The database tracks:

1. Daily Garmin expenditure and health metrics.
2. Garmin activities and activity laps.
3. Food intake and nutrition totals.
4. User profile, goals, and sync metadata.

The app is split into three Django apps:

| App | Models | Purpose |
| --- | --- | --- |
| `core` | `DailyLog`, `UserSettings` | Dashboard, settings, daily totals |
| `activities` | `Activity`, `ActivityLap` | Garmin activities, laps, running analytics |
| `nutrition` | `FineliFood`, `CustomFood`, `MealEntry` | Food diary and food database |

Most models keep `db_table = "tracker_*"` names so existing SQLite tables continue to work after the app split.

## Core Models

### UserSettings

Singleton model for profile, goals, and calculated recommendations.

Key fields:

- `weight_kg`, `height_cm`, `age`, `sex`
- `activity_level`
- `fitness_goal`
- `min_daily_calories`, `max_daily_calories`, `max_daily_deficit`
- `garmin_calorie_adjustment` (manual kcal/day correction applied to Garmin expenditure)
- `protein_goal_g`, `carbs_goal_g`, `fat_goal_g`
- `last_garmin_sync`

Use:

```python
from core.models import UserSettings

settings = UserSettings.get_settings()
target = settings.calculate_target_calories()
macros = settings.calculate_recommended_macros()
```

Rule: always use `UserSettings.get_settings()` instead of creating settings rows manually.

### DailyLog

One row per date. Aggregates Garmin daily stats and nutrition totals.

Key fields:

- `date`
- `calories_out`
- `calories_in`
- `protein_g`, `carbs_g`, `fat_g`, `sat_fat_g`
- `steps`, `distance_meters`
- `moderate_intensity_minutes`, `vigorous_intensity_minutes`
- `resting_heart_rate`, `min_heart_rate`, `max_heart_rate`

Computed property:

```python
balance = log.balance_kcal  # calories_in - calories_out
```

Rule: do not manually maintain `calories_in` or macro totals. Create/update `MealEntry` rows, then call `log.recalculate_from_meals()` when needed.

DailyLog has no user: its stored nutrition totals (and `balance_kcal`) include every user's `MealEntry` rows for that date. Never display them as one user's intake - use `nutrition.aggregation.user_daily_totals(user, start, end)`, which sums only that user's entries.

## Activities Models

### Activity

One row per Garmin activity.

Key fields:

- `activity_id` - unique Garmin activity ID
- `activity_type` - running, walking, cycling, strength, dancing, other
- `date`, `start_time`, `name`
- `duration_minutes`, `distance_km`
- `pace_min_per_km`, `avg_speed_kmh`
- `avg_hr`, `max_hr`
- `calories`
- `elevation_gain`, `elevation_loss`
- `avg_cadence`, `steps`, `vo2max`
- `hr_zone1_seconds` through `hr_zone5_seconds`

### ActivityLap

One row per lap/interval inside an activity.

Key fields:

- `activity` - FK to `Activity`
- `lap_number`
- `step_type`
- `lap_time_seconds`, `cumulative_time_seconds`
- `distance_km`
- `avg_pace_min_per_km`, `avg_speed_kmh`
- `avg_hr`, `max_hr`
- `total_ascent_m`, `total_descent_m`
- `avg_cadence`

Step type choices:

- `warmup`
- `run`
- `cooldown`
- `interval`
- `recovery`
- `rest`
- `other`

Data integrity:

- `activity` + `lap_number` is unique.
- Display pace uses `ActivityLap.pace_formatted()` and returns `MM:SS.xx`.
- Group average pace should be weighted by total time / total distance, not by averaging lap paces.

## Nutrition Models

### FineliFood

Cached or pre-imported Fineli food data.

Key fields:

- `fineli_id`
- `name_fi`, `name_sv`, `name_en`
- `energy_kcal`
- `protein_g`, `carbs_g`, `sugar_g`
- `fat_g`, `sat_fat_g`
- `fiber_g`, `salt_g`
- usage/favorite metadata where available

Values are stored per 100g.

### CustomFood

User-created food definitions.

Key fields:

- `name`, `brand`
- `energy_kcal_per_100g`
- `protein_per_100g`, `carbs_per_100g`, `sugar_per_100g`
- `fat_per_100g`, `sat_fat_per_100g`
- `fiber_per_100g`, `salt_per_100g`
- `default_portion_g`
- `use_count`

### MealEntry

Individual food log entries.

Key fields:

- `daily_log` - FK to `DailyLog`
- `fineli_food` - optional FK to `FineliFood`
- `custom_food` - optional FK to `CustomFood`
- `source_type` - fineli, custom, manual
- `meal_type` - breakfast, lunch, dinner, snack, evening_snack
- `food_name`, `portion_grams`
- `calories`, `protein_g`, `carbs_g`, `sugar_g`
- `fat_g`, `sat_fat_g`, `fiber_g`, `salt_g`

When saved with a linked Fineli/custom food, nutrition is calculated from per-100g values and `portion_grams`.

## Common Workflows

### Import Garmin CSV Files

```bash
cd backend
python manage.py import_garmin_csv
```

Default files:

- `backend/data/garmin_daily.csv`
- `backend/data/garmin_activities.csv`
- `backend/data/garmin_activity_laps.csv`

Regenerate them first when needed:

```bash
cd backend
python ../get_garmindata.py --start-date 2026-03-17 --end-date 2026-07-21
python manage.py import_garmin_csv
```

### Import Complete Fineli Dataset

```bash
cd backend
python manage.py import_fineli_csv --data-folder "path/to/Fineli_Rel20"
```

Update existing rows:

```bash
python manage.py import_fineli_csv --data-folder "path/to/Fineli_Rel20" --update-existing
```

### Add A Manual Meal Entry

```python
from core.models import DailyLog
from nutrition.models import MealEntry
from datetime import date

log, _ = DailyLog.objects.get_or_create(date=date.today())

MealEntry.objects.create(
    daily_log=log,
    source_type="manual",
    meal_type="lunch",
    food_name="Homemade pasta",
    portion_grams=300,
    calories=450,
    protein_g=15,
    carbs_g=75,
    fat_g=10,
)

log.recalculate_from_meals()
```

### Query Running Analytics Laps

```python
from activities.models import ActivityLap

work_laps = ActivityLap.objects.filter(
    activity__activity_type="running",
    step_type__in=["run", "interval"],
    distance_km__gt=0,
    lap_time_seconds__gt=0,
)
```

Add warm-up/cool-down when the analytics filters are enabled:

```python
step_types = ["run", "interval", "warmup", "cooldown"]
```

## Data Integrity Rules

- `DailyLog.date` is unique.
- `UserSettings` is a singleton.
- `Activity.activity_id` is unique.
- `ActivityLap.activity + lap_number` is unique.
- `FineliFood.fineli_id` is unique.
- `MealEntry.portion_grams` is required for nutrition calculation.
- Generated Garmin CSV files are treated as import artifacts, not hand-edited source data.
- Pace values are stored as decimal minutes per km and displayed as `MM:SS.xx`.

## Maintenance

### Migrations

```bash
cd backend
python manage.py makemigrations
python manage.py migrate
```

### Django Checks

```bash
cd backend
python manage.py check
```

### Model Smoke Checks

```bash
cd backend
python manage.py shell -c "from activities.models import Activity, ActivityLap; print(Activity.objects.count(), ActivityLap.objects.count())"
python manage.py shell -c "from nutrition.models import FineliFood; print(FineliFood.objects.count())"
```

## Troubleshooting

### Garmin CSV missing

Regenerate files:

```bash
cd backend
python ../get_garmindata.py
```

Then import:

```bash
python manage.py import_garmin_csv
```

### Duplicate activity/lap rows

The importer uses `update_or_create`, so rerunning imports should update existing activities/laps instead of duplicating them.

### Meal totals look stale

```python
log.recalculate_from_meals()
```

### Fineli import finds no files

Use the extracted Fineli release folder, not a single CSV file:

```bash
python manage.py import_fineli_csv --data-folder "C:\path\to\Fineli_Rel20"
```
