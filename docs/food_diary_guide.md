# Food Diary - User Guide

Last updated: 2026-10-11

## Overview

The Food Diary logs what you eat against the **locally imported** Fineli food database. There is no live Fineli API call - search, portions and nutrient values all come from the SQLite database, so the diary works fully offline once Fineli has been imported (see [First-time setup](#first-time-setup)).

The diary requires login. Meal entries, custom foods/recipes and favorites belong to the logged-in user.

## First-time setup

1. Create a login user (once):
   ```bash
   cd backend
   python manage.py createsuperuser
   ```
2. Import the Fineli food database (once, and again when THL publishes a new release):
   ```bash
   python manage.py import_fineli_csv --data-folder "path\to\Fineli_Rel20"
   ```
   Use the extracted Fineli Open Data folder (the one containing `component_value.csv`). The 74-nutrient package is the most complete. See [fineli_complete_import.md](fineli_complete_import.md).
3. Check the import:
   ```bash
   python manage.py shell -c "from nutrition.models import FineliFood; print(FineliFood.objects.count())"
   ```
   Expected: about 4,238 foods. **If this prints 0, food search returns nothing.**

## Daily use

1. Open http://127.0.0.1:8000/food-diary/ and log in.
2. Pick a date (date picker or previous/next buttons). Today is the default.
3. Click **+ Add Food** on a meal section: Breakfast, Lunch, Dinner, Snack, Evening Snack. Imported diaries can add extra sections (see [Diary CSV import](#diary-csv-import)).
4. Choose a tab in the Add Food dialog, pick a food, enter the portion.
5. Day and meal totals, and the dashboard's `DailyLog`, update automatically.

### Add Food tabs

| Tab | What it does |
| --- | --- |
| 🔍 Search Fineli | Type 2+ characters. Searches Finnish, Swedish and English names in the local database (top 20 matches, most-used first). An empty search lists your most-used Fineli foods. |
| ⭐ My Foods | Your own custom foods and recipes. |
| ★ Favorites | Foods you have starred. Favorites are explicit - star a food to add it, unstar to remove it. |
| 🕒 Recent | Foods you logged most recently. |
| 🔁 Frequent | Foods you log most often. |
| 📥 Diary CSV | Bulk import of Fineli diary exports. |
| ✏️ Manual Entry | One-off food with values typed in. |

### Portion units

Fineli and custom foods are stored per 100 g, and every entry is stored in grams. Other units are rough conversions:

| Unit | Converted as |
| --- | --- |
| g | exact |
| dl | 100 g per dl |
| kpl (piece) | 50 g per piece |
| rkl (tablespoon) | 15 g |
| tl (teaspoon) | 5 g |

Use grams when you can weigh the food - `dl` and `kpl` are only estimates.

### Manual entry

For a food not in Fineli and not worth saving as a custom food. Food name, portion (g), calories, protein and carbs are required; other fields are optional. **Values are for the whole portion, not per 100 g.**

### Deleting an entry

Click 🗑️, then click it again within 3 seconds. There are no browser pop-ups; feedback is shown as toast messages.

## Custom foods and recipes

Manage at http://127.0.0.1:8000/food-diary/custom-foods/ (Nutrition → Foods).

- **Custom food**: your own product with nutrient values per 100 g.
- **Recipe**: a custom food built from ingredients (Fineli foods and/or your other custom foods). **Recalculate recipe** computes per-100 g values from the ingredients. You can also edit the final values by hand. A blank value means *unknown* and is never treated as zero.

## Diary CSV import

Imports food-level rows from Fineli diary CSV exports (from the Fineli website's diary).

1. Food Diary → **📥 Import CSV** (or the Diary CSV tab).
2. Select one or more CSV files and click **Import**.
3. The page refreshes when done.

Behavior:
- Each food row becomes a meal entry with calories, protein, carbs, fat, saturated fat, sugar, fiber, salt and the auxiliary micronutrients, plus the original Fineli source details.
- Energy is converted from kJ to kcal. Below-detection-limit values (`<0.1`) are kept as such, not rounded to a plain number.
- The 5 standard Finnish meal names map to Breakfast/Lunch/Dinner/Snack/Evening Snack. Any other meal name (e.g. `Herkku`) gets its own section using the CSV's own name.
- Re-importing a date replaces only that date's earlier **CSV-imported** rows. Entries you added by hand are never touched.

Endpoint: `POST /food-diary/api/bulk-import/` (login required).

## Troubleshooting

**Search returns nothing.** The Fineli table is empty - run `import_fineli_csv` (see [First-time setup](#first-time-setup)).

**Fineli calories look about 4x too high.** The Fineli database was imported before the 2026-10-11 kJ→kcal fix. Re-run the import with `--update-existing`. Meal entries logged before the fix keep their stored values.

**My entries disappeared.** Entries belong to the user who created them. Check which account you are logged in as (shown on the Log out button).

**Redirected to the admin login page.** Expected - the app uses Django admin's login.

## Data and privacy

- Everything is stored in the local SQLite database. Nothing is sent to Fineli or anywhere else.
- `DailyLog` daily totals currently sum every user's meal entries for a date. With a single person using the app this makes no difference. See "Known Issues" in [project_status.md](project_status.md).

## Attribution

Food data: Finnish Institute for Health and Welfare (THL), Fineli® - https://fineli.fi/ - CC-BY 4.0.
