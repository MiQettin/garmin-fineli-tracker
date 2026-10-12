# Documentation Index

Last updated: 2026-10-12

## Start here

- [project_status.md](project_status.md) - what works, recent changes, decisions, known issues and next work.

## Using the app

- [food_diary_guide.md](food_diary_guide.md) - the food diary, products, recipes, favorites, diary CSV import, troubleshooting.
- [fineli_complete_import.md](fineli_complete_import.md) - importing Fineli Open Data (required for food search).

## Running and maintaining it

- [database_guide.md](database_guide.md) - data model, rules the code relies on, **safe database operations**, backups and restore, read-only health checks, migrations.
- [deployment.md](deployment.md) - moving the database to a server, shipping code updates, replacing the server database with a fresh copy, migration rules.

## Developing it

- [project_foundation.md](project_foundation.md) - architecture, app structure, data flow.
- [ui_guidelines.md](ui_guidelines.md) - design system, toasts, confirmations, pickers, mobile and testing checklist.

## Licensing

- [../LICENSE](../LICENSE) - PolyForm Noncommercial License 1.0.0 for HealthManager's own code (noncommercial use only).
- [../THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) - third-party libraries, Fineli (CC BY 4.0) and Garmin notices.

## Quick start

```bash
pip install -r requirements.txt
cd backend
python manage.py migrate
python manage.py createsuperuser
python manage.py import_fineli_csv --data-folder "path\to\Fineli_Rel20"
python manage.py runserver
```

Garmin data (optional, personal-use feature):

```bash
cd backend
python ../get_garmindata.py                     # last 30 completed days; --start-date/--end-date for backfills
python manage.py import_garmin_csv
```

Database care:

```bash
cd backend
python manage.py backup_db                      # snapshot to backend/backups/
python manage.py makemigrations --check --dry-run
python manage.py test                           # in-memory test database, never touches db.sqlite3
```

## Pages

| Page | Address |
| --- | --- |
| Dashboard | http://127.0.0.1:8000/ |
| Settings | http://127.0.0.1:8000/settings/ |
| Activities | http://127.0.0.1:8000/activities/ |
| Running analytics | http://127.0.0.1:8000/analytics/running/ |
| Health & recovery | http://127.0.0.1:8000/health/ |
| Food diary | http://127.0.0.1:8000/food-diary/ |
| Foods (own products) | http://127.0.0.1:8000/food-diary/custom-foods/ |
| Recipes | http://127.0.0.1:8000/food-diary/recipes/ |
| Favorites | http://127.0.0.1:8000/food-diary/favorites/ |
| Admin | http://127.0.0.1:8000/admin/ |

## Sensitive local files

Never commit: `.env`, `.garmintokens/`, `backend/db.sqlite3` and any other `*.sqlite3` (including `backend/backups/`), `backend/data/`, `data/`, `notes/`.

## Support

1. [project_status.md](project_status.md) - active work and known issues.
2. [database_guide.md](database_guide.md) - data questions; run its health checks.
3. [food_diary_guide.md](food_diary_guide.md) - nutrition and Fineli workflows.
4. The browser console for frontend problems.

## Attribution

Food data source: Finnish Institute for Health and Welfare (THL), Fineli. License: Creative Commons 4.0 Attribution (CC-BY). Website: https://fineli.fi/
