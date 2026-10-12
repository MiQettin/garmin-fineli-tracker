# HealthManager

A local-first health and fitness tracking application that combines Garmin activity data with nutritional tracking to provide comprehensive insights into your energy balance, macros, and recovery status.

> [!IMPORTANT]
> **The Garmin features are built for the author's own personal use.** They are tailored to one account and one set of devices and workouts (for example, the running analytics' workout categories), and they use the **unofficial** [`garminconnect`](https://github.com/cyberjunky/python-garminconnect) library rather than an official Garmin API. They are shared as-is, may not work for your account, and may break whenever Garmin changes its service. They will be generalized for public use later on.
>
> The **food diary** (Fineli search, own products, recipes, favorites, diary CSV import) works on its own, without any Garmin account.
>
> HealthManager is not affiliated with, endorsed by, or supported by Garmin or THL. See [Third-party notices](#-license-and-third-party-notices).

## 🎯 Overview

HealthManager is a privacy-focused Django web application that helps you:
- Log what you eat against the Finnish Fineli food database, your own products and your own recipes
- Track energy expenditure from Garmin device data
- Follow daily and weekly energy balance and macros against your goal
- Analyze activities, running laps and recovery (resting heart rate, sleep, HRV)

**Privacy First**: All data stays on your own machine or home server. No cloud services, no data sharing.

## ✨ Features

### Food diary
- **Fineli search**: the whole Fineli database imported locally (about 4,200 foods) - works offline. Case and ä/ö/å don't matter, every word matches in any order ("maito lak"), best matches first.
- **Portions**: Fineli's own portion sizes (pieni/iso kpl, annos, dl, rkl …), product packages ("1 × pakkaus"), ml/dl/l for liquids, recipe servings ("1 × annos"), or grams.
- **Own products**: values per 100 g or per 100 ml from the label, optional package size.
- **Recipes**: build dishes from Fineli foods and your own foods; values per 100 g are calculated (optionally from the finished dish's weight) and kept up to date when ingredients change; optional servings; duplicate and delete.
- **Favorites, Recent, Frequent**, manual entries, editing entries, and **bulk import** of Fineli diary CSV exports.
- Unknown nutrient values stay unknown - never counted as zero.

### Dashboard
- Last 7 completed days: intake vs Garmin expenditure, average daily balance against your goal (preset or custom kcal/day), colored green → yellow → red by your warning thresholds.
- Macro goals and energy shares, logged-day coverage, and a Daily Breakdown of all 7 days compared with the 7-day average.
- Manual Garmin calorie adjustment if your device over- or underestimates.
- Recovery: 7-day weighted resting-HR baseline and consecutive elevated days; training summary.

### Garmin (personal use, see the notice above)
- `get_garmindata.py` fetches activities, laps, daily stats and health metrics (sleep, HRV, stress, Body Battery, respiration, SpO2) with the unofficial `garminconnect` library and token reuse; one click with **Sync Garmin**.
- Running analytics with workout and lap filters, weighted pace.

## 🚀 Quick Start

### Prerequisites

- **Python 3.12 or newer** - required. Django 6 and `garminconnect` do not install on Python 3.11 or older (pip fails with *"No matching distribution found for Django==..."*).
- Git
- Optional: a Garmin Connect account, if you want Garmin data (personal-use feature, see the notice at the top). The food diary works without one.

**Check your Python version first:**

```bash
python3 --version     # Linux / macOS / Raspberry Pi
py --list             # Windows: lists installed versions
```

If it shows 3.11 or older, install a newer Python before continuing:

| System | Python it ships | What to do |
| --- | --- | --- |
| Raspberry Pi OS / Debian 13 "Trixie" | 3.13 | OK - `sudo apt install python3-venv` |
| Raspberry Pi OS / Debian 12 "Bookworm" | 3.11 | Too old - upgrade the OS to Trixie |
| Ubuntu 24.04 or newer | 3.12+ | OK - `sudo apt install python3-venv` |
| Windows | - | Install 3.12+ from [python.org](https://www.python.org/downloads/) |
| macOS | - | Install 3.12+ from [python.org](https://www.python.org/downloads/) or Homebrew |

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/MiQettin/garmin-fineli-tracker.git
   cd garmin-fineli-tracker
   ```

2. **Create a virtual environment with Python 3.12+**

   Linux / macOS / Raspberry Pi:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   python --version          # must show 3.12 or newer
   ```

   Windows (PowerShell):
   ```powershell
   py -3.12 -m venv venv     # or -3.13
   .\venv\Scripts\Activate.ps1
   python --version          # must show 3.12 or newer
   ```

   If PowerShell blocks the activation script, allow scripts for your user account and run the activation command again:
   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   .\venv\Scripts\Activate.ps1
   ```

   If `python --version` shows an older version, delete the `venv` folder and recreate it with the right Python.

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Create the `.env` file**
   ```bash
   cp .env.example .env      # Linux / macOS / Raspberry Pi
   copy .env.example .env    # Windows
   ```
   The defaults work for local use. `GARMIN_EMAIL` and `GARMIN_PASSWD` are only needed for Garmin data. For a server, see [docs/deployment.md](docs/deployment.md) (`DEBUG=False`, `SECRET_KEY`, `ALLOWED_HOSTS`, `DATABASE_PATH`).

5. **Create the database**
   ```bash
   cd backend
   python manage.py migrate
   ```

6. **Create a login user** (every page requires login)
   ```bash
   python manage.py createsuperuser
   ```

7. **Import the Fineli food database** (required for food search)

   Download the Basic Package from https://fineli.fi/fineli/en/avoin-data, extract it, then:
   ```bash
   python manage.py import_fineli_csv --data-folder "path/to/Fineli_Rel20"
   ```
   About 4,200 foods and their portion sizes are imported. See [docs/fineli_complete_import.md](docs/fineli_complete_import.md).

8. **Start the development server**
   ```bash
   python manage.py runserver
   ```

9. **Open** http://127.0.0.1:8000/ and log in. Fill in your body data and goals on the Settings page (created automatically with defaults on first visit).

To run it on a server (e.g. a Raspberry Pi) with `DEBUG=False`, gunicorn and a separate database location, follow [docs/deployment.md](docs/deployment.md).

## 📊 Usage

### Fetching Garmin Data

> [!NOTE]
> Personal-use feature (see the notice at the top). It uses the unofficial `garminconnect` library with **your own** Garmin Connect credentials, which may conflict with Garmin's terms of use - use it at your own risk.

`python get_garmindata.py` is the primary way Garmin data gets into the app - it logs in to Garmin Connect (reusing saved tokens from `.garmintokens/` where possible), and in one run:
- Writes `garmin_daily.csv` and `garmin_activities.csv`/`garmin_activity_laps.csv` to `backend/data/` (these need the import step below).
- Writes health metrics (sleep, HRV, stress, Body Battery, respiration, SpO2) straight to the database - no separate import step for those.

The dashboard's Sync button runs this same script plus the import step for you; the manual commands below are for backfills or troubleshooting.

For the full backfill workflow and the current project notes, see [docs/project_status.md](docs/project_status.md).

To backfill a specific range once, pass explicit dates:

```bash
python get_garmindata.py --start-date 2026-03-17 --end-date 2026-07-22
```

You can also use Finnish-style dates:

```bash
python get_garmindata.py --start-date 17.03.2026 --end-date 22.07.2026
```

### Importing Data to Database

```bash
cd backend
python manage.py import_garmin_csv
```

### Importing a Fineli Diary Export

Import your food diary from Fineli diary CSV exports (food by food, not just daily totals):

1. Go to Food Diary page: http://127.0.0.1:8000/food-diary/
2. Click "📥 Import CSV" button in the header
3. Select one or multiple CSV files from Fineli diary export
4. Click "Import" - all days are processed automatically
5. Page refreshes after successful import

**Features:**
- Multi-file upload support
- Adds missing food rows and updates matching CSV-imported rows without deleting existing entries (manual/Fineli/custom entries are untouched)
- Imports individual food rows (not just daily totals): calories, protein, carbs, fat, saturated fat, sugar, fiber, salt, plus auxiliary micronutrients and original Fineli source provenance where present
- Preserves below-detection-limit values (e.g. Fineli's `<0.1`) as a qualifier instead of collapsing them to a plain number
- Uses the diary's own meal-slot name - the 5 standard Finnish names map onto the app's Breakfast/Lunch/Dinner/Snack/Evening Snack sections; any other name (a custom-renamed slot, "Herkku", etc.) gets its own section named after the CSV's own text

### Daily Workflow

1. Log meals in Nutrition → Diary as you eat; keep your own products in Nutrition → Foods and recipes in Nutrition → Recipes - see [docs/food_diary_guide.md](docs/food_diary_guide.md)
2. Press **Sync Garmin** in the top nav to pull the latest Garmin data
3. Check the Dashboard: the last 7 completed days (the window ends yesterday, since today is still incomplete), energy balance, macros and recovery
4. Adjust body data and goals on the Settings page as needed

## 🍎 Fineli Integration

### About Fineli Open Data

Fineli is the Finnish food composition database maintained by the Finnish Institute for Health and Welfare (THL). The data is freely available as open data under the Creative Commons 4.0 Attribution (CC-BY) license. HealthManager imports it as CSV (`python manage.py import_fineli_csv`) rather than calling the live API, so food search works fully offline.

### What's Built

- **Food Search**: search across the imported Fineli database (Finnish, Swedish and English names).
- **Quick Add**: log a Fineli food with one of its portion sizes; macros and micronutrients are filled in from the database.
- **Recipes**: build dishes from Fineli foods and your own products and recipes - values recalculate from the ingredient list.
- **Favorites, Recent, and Frequent**: quick-access lists for foods you log often.
- **Bulk Diary CSV Import**: upload one or more Fineli diary export CSVs - see "Importing a Fineli Diary Export" above.

### Data Packages

Fineli provides downloadable CSV packages:
- **Basic Package 1**: 4,232 foods with 55 nutrients (1.5 MB)
- **Basic Package 2**: 4,232 foods with 74 nutrients (1.9 MB)
- **Raw Materials**: 1,370 basic ingredients (0.5 MB)

### Attribution

Fineli data is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), which requires attribution:
> "Nutritional data provided by the Finnish Institute for Health and Welfare (THL), Fineli®"

The app shows this credit on the food diary and recipe pages. HealthManager converts Fineli energy values from kJ to kcal and salt from mg to g; values are otherwise unmodified. This repository does not include any Fineli data - you download it yourself from THL.

Fineli is a registered trademark of THL. This application is independent and not affiliated with, endorsed by, or part of THL's official services.

## 🗂️ Project Structure

```
garmin-fineli-tracker/
├── backend/               # Django application
│   ├── config/           # Project settings
│   ├── core/             # Core app (DailyLog, UserSettings, dashboard)
│   │   ├── models.py     # DailyLog and UserSettings models
│   │   ├── views.py      # Dashboard, settings and Garmin sync views
│   │   ├── templates/    # Base and core templates
│   │   ├── templatetags/ # Template filters
│   │   └── management/   # backup_db command
│   ├── activities/       # Activity tracking app
│   │   ├── models.py     # Activity and ActivityLap models
│   │   ├── views.py      # Activity list, detail, analytics
│   │   ├── templates/    # Activity templates
│   │   └── management/   # import_garmin_csv command
│   ├── nutrition/        # Food diary app
│   │   ├── models.py     # Fineli catalog, products and recipes (CustomFood), diary (MealEntry), favorites
│   │   ├── views.py      # Diary, products, recipes, favorites, search and bulk CSV import
│   │   ├── aggregation.py, recipe_aggregation.py, recipe_persistence.py  # daily totals and recipe calculation
│   │   ├── templates/    # Food diary templates
│   │   └── management/   # import_fineli_csv command
│   ├── health/           # Garmin health/recovery metrics app
│   │   ├── models.py     # DailyHealth, SleepSummary, HrvSummary
│   │   ├── garmin_sync.py, garmin_parser.py, garmin_persistence.py
│   │   ├── analytics.py  # Rolling trends for the health page/API
│   │   └── views.py      # /health/ page and /api/health/ endpoints
│   ├── data/             # Generated Garmin CSV files (not in repo)
│   └── db.sqlite3        # Local SQLite database (not in repo; path overridable with DATABASE_PATH)
├── data/                 # Fineli Open Data packages and diary exports (user data, not in repo)
├── docs/                 # Project documentation
├── get_garmindata.py     # Garmin data fetch script - primary Garmin data path (activities, daily stats, health metrics)
├── requirements.txt      # Python dependencies
├── .env.example          # Environment variable template
└── README.md             # This file
```

## 📈 Data Model

The full model reference, the rules the code relies on, and how to back up, inspect and restore the database are in [docs/database_guide.md](docs/database_guide.md). In short:

- **`DailyLog`** - one row per day: Garmin expenditure (`calories_out`, active/BMR kcal), steps, distance, intensity minutes, heart rate; plus cached nutrition totals.
- **`UserSettings`** - one shared settings row: body data, activity level, fitness goal (preset or custom), warning thresholds, Garmin calorie adjustment, macro goals.
- **Nutrition** - `FineliFood` (+ micronutrients and portion sizes), `CustomFood` (your products and recipes, with ingredients), `MealEntry` (one diary entry, values stored for the portion), `FavoriteFood`.
- **Garmin** - `Activity`, `ActivityLap`, and the health tables `DailyHealth`, `SleepSummary`, `HrvSummary`.

### A note on multi-user data
Diary entries, products, recipes and favorites belong to the logged-in Django user, and the diary and dashboard totals count only that user's entries. Everything else - `UserSettings`, the Garmin, activity and health data - is shared by whoever is logged in. The app is meant for one person; with more than one account, settings, goals and Garmin data are not private per person.

## 🔧 Configuration

### Fitness Goals
The dashboard compares your average daily balance with the goal:
- **Aggressive Cut**: -500 kcal/day (significant fat loss)
- **Moderate Cut**: -300 kcal/day (standard fat loss)
- **Mild Cut**: -200 kcal/day (for lean individuals)
- **Maintenance**: 0 kcal/day
- **Lean Bulk**: +250 kcal/day (slow muscle gain)
- **Bulk**: +400 kcal/day (faster muscle gain)
- **Custom**: your own kcal/day target

Warning thresholds are automatic or a custom value ("kcal below goal"); the alert level is twice the warning threshold. A **Garmin calorie adjustment** (kcal/day, positive or negative) corrects Garmin's expenditure in every calculation without changing the imported data.

### Activity Levels
- Sedentary (1.2x): Little or no exercise
- Lightly active (1.375x): 1-3 days/week
- Moderately active (1.55x): 3-5 days/week
- Very active (1.725x): 6-7 days/week
- Extra active (1.9x): Athlete/physical job

## 🏥 Health Insights

### Energy balance
- 7-day average daily balance against the fitness goal, colored by the warning thresholds (green on track, yellow warning, red alert, blue surplus on cut goals)
- Each day compared with the 7-day average
- Macro energy shares, shown only when the macro data is complete

### Heart rate and recovery
- 7-day weighted resting-HR baseline and deviation
- Consecutive elevated-HR days
- Sleep, HRV, stress and Body Battery trends on the Health page

## 🔐 Privacy & Security

- **Local-only**: All data stored in a local SQLite database - back it up with `python manage.py backup_db` (see [docs/database_guide.md](docs/database_guide.md))
- **No telemetry**: No data sent to external services
- **Source available**: the full source is public, so you can see exactly how your data is handled (noncommercial license, see below)
- **Garmin credentials**: Stored only in environment variables, never in code
- **Login required**: every page and API endpoint requires a logged-in user (Django admin login). Page views redirect to the login page; JSON endpoints return HTTP 401.
- **Home network only**: the server setup (gunicorn + whitenoise, see [docs/deployment.md](docs/deployment.md)) runs over plain HTTP. Don't expose it to the internet without adding HTTPS first.

## 🛠️ Development

### Running Tests
```bash
cd backend
python manage.py test
```
Tests always run on an in-memory database, never on `db.sqlite3`.

### Backing Up / Moving the Database
```bash
python manage.py backup_db
```
Writes a consistent snapshot to `backend/backups/` (safe while the server runs). To move the database to a server and keep developing here, see [docs/deployment.md](docs/deployment.md).

### Making Migrations
```bash
python manage.py makemigrations
python manage.py migrate
python manage.py makemigrations --check --dry-run   # before pushing: must print "No changes detected"
```
Every model change ships with its migration; a server is updated with `backup_db`, `git pull`, `migrate` ([docs/deployment.md](docs/deployment.md)).

### Creating Admin User
```bash
python manage.py createsuperuser
```
Access admin panel at: http://127.0.0.1:8000/admin/

## 📝 Current Status

See [docs/project_status.md](docs/project_status.md) for the up-to-date phase, recent changes, and known issues - it's kept current as the app changes; this section only summarizes what's working.

### What Works
✅ Food diary: Fineli search, portion sizes, own products, recipes (servings, finished weight, automatic recalculation), favorites, editing, diary CSV import
✅ Per-user diary totals on the diary and dashboard
✅ Dashboard: energy balance against preset or custom goals with threshold coloring, macros, Garmin calorie adjustment
✅ Garmin data import via the unofficial `garminconnect` library (personal-use feature) - activities, daily stats, and health metrics
✅ Running analytics, heart rate baseline and recovery tracking

### Known Gaps
⚠️ Settings and Garmin/health/activity data are shared by all accounts (fine for a single person)
⚠️ Server deployment is for a home network only (gunicorn + whitenoise over HTTP) - no HTTPS setup yet

### Roadmap
- Consistent per-user data scoping (or an explicit single-user decision)
- Side-by-side workout comparison, personal records
- Training load and recovery balance
- ML/AI trend detection and forecasting (longer-term)

## 🤝 Contributing

This is a personal project, but suggestions and improvements are welcome. Contributions are accepted under the same noncommercial license as the project. The Garmin features are still tailored to the author's own use (see the notice at the top), so issues about other accounts or devices may not be addressed until they are generalized. To contribute:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## 📄 License and third-party notices

HealthManager's own code is released under the [PolyForm Noncommercial License 1.0.0](LICENSE): you may use, change and share it for any **noncommercial** purpose - personal use, study, hobby projects, research, and use by charitable, educational and public organizations. **Commercial use is not permitted**; for a commercial license, contact the copyright holder. Third-party libraries and data keep their own licenses - see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the full list.

**Trademarks and affiliation**
- Garmin®, Garmin Connect™ and Body Battery™ are trademarks of Garmin Ltd. or its subsidiaries. HealthManager is not affiliated with, endorsed by, or supported by Garmin.
- Fineli® is a registered trademark of the Finnish Institute for Health and Welfare (THL). HealthManager is not affiliated with or endorsed by THL.

## 🙏 Acknowledgments

- [Fineli®](https://fineli.fi/) - Finnish food composition database by the Finnish Institute for Health and Welfare (THL), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) by Ron Klinkien (MIT) - the unofficial Garmin Connect client behind `get_garmindata.py`, built on [garth](https://github.com/matin/garth) (MIT)
- [Django](https://www.djangoproject.com/) (BSD-3-Clause), [whitenoise](https://github.com/evansd/whitenoise) (MIT), [gunicorn](https://gunicorn.org/) (MIT)
- [Bootstrap](https://getbootstrap.com/) and [Bootstrap Icons](https://icons.getbootstrap.com/) (MIT), [Chart.js](https://www.chartjs.org/) (MIT)

## 📞 Support

For issues or questions, please refer to the documentation in the `docs/` folder:
- [project_foundation.md](docs/project_foundation.md) - Core principles and design
- [project_status.md](docs/project_status.md) - Current development status
- [food_diary_guide.md](docs/food_diary_guide.md) - Using the food diary, products, recipes and favorites
- [database_guide.md](docs/database_guide.md) - Data model, safe database operations, backups and health checks
- [deployment.md](docs/deployment.md) - Moving the database to a server, shipping updates, replacing the server database

---

**Note**: Garmin data is entirely optional - the food diary, recipes, and dashboard work without a Garmin account. The Garmin features are currently tailored to the author's personal use and will be generalized later. If you do connect one, `get_garmindata.py` logs in to Garmin Connect with the `garminconnect` library and reuses saved tokens where possible, so you don't need to re-authenticate on every run.
