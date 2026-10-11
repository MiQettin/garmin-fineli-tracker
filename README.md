# HealthManager

A local-first health and fitness tracking application that combines Garmin activity data with nutritional tracking to provide comprehensive insights into your energy balance, macros, and recovery status.

> [!IMPORTANT]
> **The Garmin features are built for the author's own personal use.** They are tailored to one account and one set of devices and workouts (for example, the running analytics' workout categories), and they use the **unofficial** [`garminconnect`](https://github.com/cyberjunky/python-garminconnect) library rather than an official Garmin API. They are shared as-is, may not work for your account, and may break whenever Garmin changes its service. They will be generalized for public use later on.
>
> The **food diary** (Fineli search, custom foods, recipes, diary CSV import) works on its own, without any Garmin account.
>
> HealthManager is not affiliated with, endorsed by, or supported by Garmin or THL. See [Third-party notices](#-license-and-third-party-notices).

## 🎯 Overview

HealthManager is a privacy-focused Django web application that helps you:
- Track energy expenditure from Garmin device data
- Log nutritional intake and macros
- Monitor daily and weekly energy balance
- Analyze activity patterns and heart rate data
- Receive personalized health insights and alerts

**Privacy First**: All data stays on your local machine. No cloud services, no data sharing.

## ✨ Features

### Core Tracking
- **Garmin Integration**: `get_garmindata.py` fetches activities, daily stats, and health metrics (sleep, HRV, stress, Body Battery, respiration, SpO2) from Garmin Connect using the unofficial `garminconnect` library, with token reuse so you don't re-login every time. This is the app's actual, currently-used data path - it can be run manually or triggered with one click from the Sync Garmin button. *Currently tailored to the author's own use - see the notice at the top.*
- **Nutritional Logging**: Fineli database search, custom foods and recipes, manual entries, favorites, and bulk diary CSV import (see below) - all against the local Fineli data, no live API dependency.
- **Daily Dashboard**: View calories in vs out, macro breakdown, and activity summary
- **Weekly Summary**: 7-day rolling analysis with balance calculations

### Advanced Insights
- **Heart Rate Baseline**: 7-day weighted average with deviation tracking
- **Recovery Status**: Consecutive elevated HR detection and recovery monitoring
- **Rolling Deficit Tracking**: 3-day and 7-day average deficit calculations
- **Under-fueling Alerts**: Detection of high activity combined with sustained caloric deficit
- **Macro Goal Tracking**: Automated TDEE calculation with customizable fitness goals

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
   venv\Scripts\activate
   python --version          # must show 3.12 or newer
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
   About 4,200 foods are imported. See [docs/fineli_complete_import.md](docs/fineli_complete_import.md).

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

### Importing Fineli Nutrition Data

Import daily nutrition totals from Fineli diary CSV exports:

1. Go to Food Diary page: http://127.0.0.1:8000/food-diary/
2. Click "📥 Import CSV" button in the header
3. Select one or multiple CSV files from Fineli diary export
4. Click "Import" - all days are processed automatically
5. Page refreshes after successful import

**Features:**
- Multi-file upload support
- Updates existing days or creates new ones (re-importing a date replaces only its own prior bulk-import rows; manual/Fineli/custom entries are untouched)
- Imports individual food rows (not just daily totals): calories, protein, carbs, fat, saturated fat, sugar, fiber, salt, plus auxiliary micronutrients and original Fineli source provenance where present
- Preserves below-detection-limit values (e.g. Fineli's `<0.1`) as a qualifier instead of collapsing them to a plain number
- Uses the diary's own meal-slot name - the 5 standard Finnish names map onto the app's Breakfast/Lunch/Dinner/Snack/Evening Snack sections; any other name (a custom-renamed slot, "Herkku", etc.) gets its own section named after the CSV's own text

### Daily Workflow

1. Log meals in Nutrition → Diary as you eat - see [docs/food_diary_guide.md](docs/food_diary_guide.md)
2. Press **Sync Garmin** in the top nav to pull the latest Garmin data
3. Check the Dashboard: the last 7 completed days (the window ends yesterday, since today is still incomplete), energy balance, macros and recovery
4. Adjust body data and goals on the Settings page as needed

## 🍎 Fineli Integration

### About Fineli Open Data

Fineli is the Finnish food composition database maintained by the Finnish Institute for Health and Welfare (THL). The data is freely available as open data under the Creative Commons 4.0 Attribution (CC-BY) license. HealthManager imports it as CSV (`python manage.py import_fineli_csv`) rather than calling the live API, so food search works fully offline.

### What's Built

- **Food Search**: Type-ahead search across the imported Fineli database (Finnish names).
- **Quick Add**: Add a Fineli food to the diary with a portion size, auto-filling macro and micronutrient values.
- **Custom Foods & Recipes**: Save your own foods, and build recipes from Fineli foods and/or other custom foods - composition recalculates from the ingredient list.
- **Favorites, Recent, and Frequent**: Quick-access lists for foods you log often.
- **Bulk Diary CSV Import**: Upload one or more Fineli diary export CSVs from the Food Diary page - see "Importing Fineli Nutrition Data" below.

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
│   │   ├── views.py      # Dashboard and settings views
│   │   ├── templates/    # Base and core templates
│   │   └── templatetags/ # Template filters
│   ├── activities/       # Activity tracking app
│   │   ├── models.py     # Activity and ActivityLap models
│   │   ├── views.py      # Activity list, detail, analytics
│   │   ├── templates/    # Activity templates
│   │   └── management/   # import_garmin_csv command
│   ├── nutrition/        # Food diary app
│   │   ├── models.py     # FineliFood, CustomFood, MealEntry, recipes, favorites
│   │   ├── views.py      # Food diary, recipe, and bulk CSV import views
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

### DailyLog
Stores one entry per day with:
- Energy: calories_in, calories_out, balance
- Macros: protein_g, carbs_g, fat_g, sat_fat_g
- Activity: steps, distance_meters, moderate/vigorous intensity minutes
- Heart Rate: resting_heart_rate, min/max heart rate
- Derived: hr_baseline_7d, rolling_deficit_3d, rolling_deficit_7d

### UserSettings
Singleton model containing:
- Physical data: weight, height, age, sex
- Calorie thresholds and goals
- Activity level and fitness goals
- Macro targets (protein, carbs, fat)
- Display preferences

### A note on multi-user data
`MealEntry`, `CustomFood`, and `FavoriteFood` (all in `nutrition`) are scoped to the logged-in Django user. Everything else - `DailyLog`, `UserSettings`, and all Garmin/activity/health data (`Activity`, `ActivityLap`, `DailyHealth`, `SleepSummary`, `HrvSummary`) - is global and shared by whoever is logged in, with no per-user separation. `DailyLog.recalculate_from_meals()` in particular sums every user's meal entries together. This app was originally single-user; if you're using it with more than one account, be aware that food diary totals and all Garmin/health data are currently shared across accounts, not private per person.

## 🔧 Configuration

### Fitness Goals
The app supports various fitness goals with automatic calorie adjustment:
- **Aggressive Cut**: -500 kcal/day (significant fat loss)
- **Moderate Cut**: -300 kcal/day (standard fat loss)
- **Mild Cut**: -200 kcal/day (for lean individuals)
- **Maintenance**: 0 kcal/day
- **Lean Bulk**: +250 kcal/day (slow muscle gain)
- **Bulk**: +400 kcal/day (faster muscle gain)

### Activity Levels
- Sedentary (1.2x): Little or no exercise
- Lightly active (1.375x): 1-3 days/week
- Moderately active (1.55x): 3-5 days/week
- Very active (1.725x): 6-7 days/week
- Extra active (1.9x): Athlete/physical job

## 🏥 Health Insights

The app provides several automated health insights:

### Rolling Deficit Monitoring
- 3-day and 7-day average deficit tracking
- Alerts for sustained deficits exceeding safe thresholds
- Color-coded indicators (green/yellow/red)

### Heart Rate Analysis
- 7-day weighted baseline calculation
- Deviation tracking from baseline
- Consecutive elevated HR detection for recovery status

### Under-fueling Detection
- Identifies high activity days with inadequate calorie intake
- Combines activity metrics with deficit tracking

## 🔐 Privacy & Security

- **Local-only**: All data stored in local SQLite database
- **No telemetry**: No data sent to external services
- **Open source**: Full transparency of data handling
- **Garmin credentials**: Stored only in environment variables, never in code
- **Login required**: every page and API endpoint requires a logged-in user (Django admin login). Page views redirect to the login page; JSON endpoints return HTTP 401.
- **Home network only**: the server setup (gunicorn + whitenoise, see [docs/deployment.md](docs/deployment.md)) runs over plain HTTP. Don't expose it to the internet without adding HTTPS first.

## 🛠️ Development

### Running Tests
```bash
cd backend
python manage.py test
```

### Backing Up / Moving the Database
```bash
python manage.py backup_db
```
Writes a consistent snapshot to `backend/backups/` (safe while the server runs). To move the database to a server and keep developing here, see [docs/deployment.md](docs/deployment.md).

### Making Migrations
```bash
python manage.py makemigrations
python manage.py migrate
```

### Creating Admin User
```bash
python manage.py createsuperuser
```
Access admin panel at: http://127.0.0.1:8000/admin/

## 📝 Current Status

See [docs/project_status.md](docs/project_status.md) for the up-to-date phase, recent changes, and known issues - it's kept current as the app changes; this section only summarizes what's working.

### What Works
✅ Garmin data import via the unofficial `garminconnect` library (`get_garmindata.py`, personal-use feature) - activities, daily stats, and health metrics (sleep, HRV, stress, Body Battery, respiration, SpO2)
✅ Fineli food search, custom foods, recipes, favorites, and bulk diary CSV import
✅ Daily and weekly views, running analytics
✅ Heart rate baseline and recovery tracking
✅ Rolling deficit calculations
✅ Activity summary display
✅ Personalized insights and alerts

### Known Gaps
⚠️ Garmin/health/activity data and `DailyLog` totals are shared globally, not scoped per user (fine for a single person)
⚠️ Server deployment is for a home network only (gunicorn + whitenoise over HTTP) - no HTTPS setup yet

### Roadmap
- Consistent per-user data scoping (or an explicit single-user decision)
- Side-by-side workout comparison, personal records
- Training load and recovery balance
- ML/AI trend detection and forecasting (longer-term)

## 🤝 Contributing

This is a personal project, but suggestions and improvements are welcome. The Garmin features are still tailored to the author's own use (see the notice at the top), so issues about other accounts or devices may not be addressed until they are generalized. To contribute:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## 📄 License and third-party notices

HealthManager's own code is released under the [MIT License](LICENSE). Third-party libraries and data keep their own licenses - see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the full list.

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
- [food_diary_guide.md](docs/food_diary_guide.md) - Using the food diary
- [deployment.md](docs/deployment.md) - Moving the database to a server and shipping updates

---

**Note**: Garmin data is entirely optional - the food diary, recipes, and dashboard work without a Garmin account. The Garmin features are currently tailored to the author's personal use and will be generalized later. If you do connect one, `get_garmindata.py` logs in to Garmin Connect with the `garminconnect` library and reuses saved tokens where possible, so you don't need to re-authenticate on every run.
