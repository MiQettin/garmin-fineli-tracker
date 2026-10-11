# Documentation Index

Last updated: 2026-10-11

## Project Status

- [project_status.md](project_status.md) - Current phase, recent changes, known issues, and next work.

## User Guides

- [food_diary_guide.md](food_diary_guide.md) - Food diary setup and daily use, custom foods/recipes, CSV diary import, troubleshooting.
- [fineli_complete_import.md](fineli_complete_import.md) - Fineli Open Data import (required for food search).
- [deployment.md](deployment.md) - Moving the database to a server, shipping code updates, migration rules.
- [ui_guidelines.md](ui_guidelines.md) - Shared UI patterns, toast API, filter controls, and frontend checklist.
- [../notes/kaytto.txt](../notes/kaytto.txt) - Finnish quick-use notes (personal, not in Git; partly outdated - describes the old live-API Fineli search).

## Licensing

- [../LICENSE](../LICENSE) - MIT license for HealthManager's own code.
- [../THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) - Third-party libraries, Fineli (CC BY 4.0) and Garmin notices.

## Technical Guides

- [project_foundation.md](project_foundation.md) - Local-first architecture, app structure, data flow, and model scope.
- [database_guide.md](database_guide.md) - Django model guide, data integrity rules, and common workflows.

## Current Development Focus

See [project_status.md](project_status.md). In short: the food diary is in daily use, the database is getting a server copy ([deployment.md](deployment.md)), and running analytics is the main feature work.

## Quick Start

### Install Dependencies

```bash
pip install -r requirements.txt
```

### First-Time Setup

```bash
cd backend
python manage.py migrate
python manage.py createsuperuser
python manage.py import_fineli_csv --data-folder "path\to\Fineli_Rel20"
```

### Import Garmin Data

```bash
cd backend
python ../get_garmindata.py
python manage.py import_garmin_csv
```

The default Garmin fetch exports the last 30 completed days to `backend/data/`.

Backfill a specific range:

```bash
cd backend
python ../get_garmindata.py --start-date 2026-03-17 --end-date 2026-07-21
python manage.py import_garmin_csv
```

### Start Server

```bash
cd backend
python manage.py runserver
```

Open:

- Dashboard: http://127.0.0.1:8000/
- Activities: http://127.0.0.1:8000/activities/
- Run Analytics: http://127.0.0.1:8000/analytics/running/
- Food Diary: http://127.0.0.1:8000/food-diary/
- Foods: http://127.0.0.1:8000/food-diary/custom-foods/
- Settings: http://127.0.0.1:8000/settings/

## Latest Changes

See [project_status.md](project_status.md) → "Recent Changes" for the full list.

### 2026-10-11

- Login required on every page and API endpoint.
- Fixed Fineli import storing kJ as kcal (re-import with `--update-existing`).
- Added `DATABASE_PATH`, `python manage.py backup_db`, and [deployment.md](deployment.md).

### 2026-07-22

- Added Warm-up and Cool-down lap filter buttons to Run Analytics.
- Preserved workout/search filters while toggling lap inclusion filters.
- Fixed analytics template hidden-input syntax.
- Standardized pace display to `MM:SS.xx`.
- Updated analytics average pace to use weighted total time / total distance.
- Updated Garmin sync JavaScript to use the CSRF cookie and `showToast(message, type)`.
- Updated legacy Garmin fetcher with token reuse, date range arguments, and clearer HTTP 429 handling.
- Raised `garminconnect` dependency to `>=0.3.6`.

### Earlier Structural Changes

- Split the old tracker app into `core`, `activities`, and `nutrition`.
- Moved management commands into domain apps.
- Consolidated shared template tags in `core/templatetags/core_extras.py`.
- Moved generated Garmin CSV files under `backend/data/`.

## Sensitive Local Files

Do not commit:

- `.env`
- `.garmintokens/`
- `backend/db.sqlite3` and any other `*.sqlite3` (including `backend/backups/`)
- `backend/data/`
- `data/`
- `__pycache__/`

## Support

1. Check [project_status.md](project_status.md) for active work and known issues.
2. Check [database_guide.md](database_guide.md) for model/data questions.
3. Check [food_diary_guide.md](food_diary_guide.md) for nutrition and Fineli workflows.
4. Check browser console errors for frontend problems.

## Attribution

Food data source: Finnish Institute for Health and Welfare (THL), Fineli.
License: Creative Commons 4.0 Attribution (CC-BY).
Website: https://fineli.fi/
