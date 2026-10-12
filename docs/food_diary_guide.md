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

Every entry is stored in grams; nutrient values are per 100 g.

**Fineli foods** offer grams plus the food's own Fineli portion sizes, with the grams shown in the list - for example *peeled banana*: pieni (kpl) 100 g, keskikokoinen (kpl) 125 g, iso (kpl) 150 g, pieni / keskikokoinen / iso annos, and dl, rkl or tl where Fineli gives them. Pick a unit, enter how many (e.g. 2 × iso (kpl)), and the dialog shows the total grams. The server recalculates the grams from the stored Fineli size, and the diary shows the entry as e.g. "2 × iso (kpl) · 300g". Entries imported from a Fineli diary CSV show their portion the same way.

Portion sizes come from `foodaddunit.csv` in the Fineli package and are loaded by `import_fineli_csv`. A database imported before 2026-10-11 has none - re-run the import with `--update-existing`.

**Custom products** (e.g. a protein quark) offer **pakkaus** - one package, using the product's *Package size (g)* - plus grams. Enter the values from the label per 100 g and the package size once; then log "1 × pakkaus" (or 0.5) in the diary.

**Recipes** have no Fineli portion data, so they are logged in grams only - no guessed units. A recipe's page shows its total ingredient mass ("Total ingredient mass"), and its nutrient values are per 100 g, so you just enter how many grams you ate (e.g. half of an 800 g recipe = 400 g).

Editing an entry's grams by hand removes its recorded portion, since it no longer matches.

### Manual entry

For a food not in Fineli and not worth saving as a custom food. Food name, portion (g), calories, protein and carbs are required; other fields are optional. **Values are for the whole portion, not per 100 g.**

### Deleting an entry

Click 🗑️, then click it again within 3 seconds. There are no browser pop-ups; feedback is shown as toast messages.

## Custom foods and recipes

Manage at http://127.0.0.1:8000/food-diary/custom-foods/ (Nutrition → Foods).

- **Custom food**: your own product with nutrient values per 100 g (as printed on the package) and a **Package size (g)**, so the diary can log "1 × pakkaus".
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
- Re-importing a file adds only missing food rows and updates matching **CSV-imported** rows. Existing rows that are absent from a later file are preserved, and entries you added by hand are never touched.
- All selected files are validated before any database write. If one file is invalid, the complete upload is rejected. Each diary day is locked during import to prevent simultaneous uploads from creating duplicates. If pre-existing duplicate CSV rows are detected, the import stops without changing anything.

Endpoint: `POST /food-diary/api/bulk-import/` (login required).

## Troubleshooting

**Search returns nothing.** The Fineli table is empty - run `import_fineli_csv` (see [First-time setup](#first-time-setup)).

**Fineli calories look about 4x too high.** The Fineli database was imported before the 2026-10-11 kJ→kcal fix. Re-run the import with `--update-existing`. Meal entries logged before the fix keep their stored values.

**My entries disappeared.** Entries belong to the user who created them. Check which account you are logged in as (shown on the Log out button).

**Redirected to the admin login page.** Expected - the app uses Django admin's login.

## Data and privacy

- Everything is stored in the local SQLite database. Nothing is sent to Fineli or anywhere else.
- Day totals in the diary and on the dashboard count only the logged-in user's own entries, so another account logging (or importing) food on the same date can't change them.

## Attribution

Food data: Finnish Institute for Health and Welfare (THL), Fineli® - https://fineli.fi/ - CC-BY 4.0.
