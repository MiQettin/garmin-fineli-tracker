# Food Diary - User Guide

Last updated: 2026-10-12

## Overview

The food diary logs what you eat against the **locally imported** Fineli food database. There is no live Fineli API call - search, portion sizes and nutrient values all come from the SQLite database, so the diary works offline once Fineli has been imported (see [First-time setup](#first-time-setup)).

Everything requires login. Diary entries, your own products and recipes, and favorites belong to the logged-in user. "Today" follows Finnish time.

The Nutrition menu has four pages:

| Page | Address | For |
| --- | --- | --- |
| Diary | `/food-diary/` | Logging food day by day |
| Foods | `/food-diary/custom-foods/` | Your own products (values from the label) |
| Recipes | `/food-diary/recipes/` | Your recipes (values calculated from ingredients) |
| Favorites | `/food-diary/favorites/` | Starred foods for quick logging |

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

## Language

**Settings → Language** switches the whole app between English and Suomi. In Finnish, Fineli food names are always Finnish. In English, **Fineli food names** lets you choose Fineli's Finnish or English names; the names change everywhere (search, diary, recipes, favorites). Your own products and recipes keep the names you gave them. Portion unit names (e.g. *iso (kpl)* / *large piece*) follow the app language.

## Logging food

1. Open **Nutrition → Diary**. Today is selected; change the day with the date picker or the ◀ ▶ arrows. On a phone the date row stays on one line and the diary CSV import is the 📥 button.
2. To start a meal, pick a meal in the **Meal** field above the meals, or type a name and press Enter. Tapping the field lists the standard meals - Breakfast, Lunch, Snack, Dinner, Supper (*Aamiainen, Lounas, Välipala, Päivällinen, Illallinen*) - and typing filters them (*Aam* → Aamiainen). Any other name can be typed as is (e.g. *Ennen kisaa*, at most 50 characters). A typed standard name, in English or Finnish, goes to that standard meal. The new meal appears as an empty section until its first food is saved.
3. Click **+ Add Food** on the meal, choose a tab, pick a food, set the portion, and confirm.

The diary shows the meals logged on that day, in the order they were first logged. 🗑️ on a meal deletes the whole meal with its foods (click twice within 3 seconds, like deleting a food). The meal and day totals update at once. They only count your own entries.

On a wide screen the diary stays about 900 px wide, so the buttons are near the food names. Meals show only their name, no icon.

**On a phone:**
- Once the day has meals, the **Meal** field moves below them.
- Each meal has a ▾ arrow at its right edge that shows or hides its foods; a hidden meal shows its name, **+ Add Food**, 🗑️ and its totals. Meals start hidden. A meal you open stays open on that day (in the same browser tab), and **+ Add Food** opens its meal, so the food you add is shown.
- The day totals and meal cards are compact on a phone.

### Micronutrients

**Micronutrients ▾** under the day totals opens the day's minerals (iron, calcium, magnesium, potassium, zinc, selenium, iodine, phosphorus, sodium), vitamins (D, B12, folate, C, A, E, K, B1, B2, niacin, B6) and fats (omega-3, EPA, DHA, omega-6, trans fat, cholesterol). Only your own entries count. A food with no value for a nutrient (often a manual entry or an own product) is unknown, not zero, so a total that leaves some foods out shows how many of the day's foods it covers, e.g. *Iron 12 mg 8/11*. A nutrient that no food of the day reports is not listed, and the panel is hidden when none is known. A "less than" value (e.g. *<0.1*) counts with its threshold.

### Add Food tabs

| Tab | What it does |
| --- | --- |
| 🔍 Search Fineli | Type 2+ characters. Searches Finnish, Swedish and English names. Case and ä/ö/å don't matter, and every word must match in any order - "maito lak" finds *MAITO, …, LAKTOOSITON*. Shows the top 20: your most-used foods first, then the best matches (*BANAANI, KUORITTU* before *BANAANILASTU*). An empty search lists your most-used Fineli foods. |
| ⭐ My Foods | Your own products and recipes. |
| ★ Favorites | Foods you have starred (☆ on a search result). |
| 🕒 Recent | Foods you logged most recently. |
| 🔁 Frequent | Foods you log most often. |
| 📥 Diary CSV | Bulk import of Fineli diary exports - see [Diary CSV import](#diary-csv-import). |
| ✏️ Manual Entry | A one-off food with its values typed in. |

### Portion units

Every entry is stored in grams (or millilitres for per-100 ml products). The portion picker offers the units that make sense for the food and shows the resulting grams:

| Food | Units |
| --- | --- |
| Fineli food | grams, plus the food's own Fineli portion sizes - e.g. *peeled banana*: pieni (kpl) 100 g, keskikokoinen (kpl) 125 g, iso (kpl) 150 g, pieni/keskikokoinen/iso annos, and dl, rkl or tl where Fineli gives them |
| Product, values per 100 g | grams, plus **pakkaus** if it has a package size |
| Product, values per 100 ml | ml, dl, l, plus **pakkaus** if it has a package size |
| Recipe | grams, plus **annos** if it has a servings count |

The unit names above are the Finnish ones; in English they are shown as *small piece*, *package*, *serving* and so on. The server converts units to grams from the stored sizes, and the diary shows the entry as e.g. "2 × iso (kpl) · 300g". Fineli portion sizes come from `foodaddunit.csv` and are loaded by `import_fineli_csv`; a database imported before 2026-10-11 has none - re-run the import with `--update-existing`.

Nutrient values are taken from the database when you log the food, so an entry always uses the food's current values. An entry keeps those values afterwards, even if the food or recipe changes later.

### Manual entry

For a food that is not in Fineli and not worth saving as a product. Food name, portion (g), calories, protein and carbs are required; the rest are optional. **Values are for the whole portion, not per 100 g.**

### Editing an entry

Click ✏️ on an entry. You can change the meal (the same field as when adding: a standard meal or a name of your own), the name and the amount:

- **Grams** - the calories, macros and micronutrients follow the new amount. An entry logged as "2 × iso (kpl)" becomes a plain gram amount.
- **Portion count** (entries logged in portion units) - "2 × iso (kpl)" → 3 recalculates the grams and keeps the unit.
- **Edit nutrition values** - only to correct a value by hand; corrected values are kept as typed. Leave a field empty if the value is unknown.

The date of an entry can't be changed - delete it and add it again on the right day.

### Deleting an entry

Click 🗑️, then click it again within 3 seconds. There are no browser pop-ups; feedback is shown as toast messages.

## Products (Nutrition → Foods)

Products are foods you enter yourself from the label, e.g. a protein quark or a maple syrup.

- **Nutrition values per**: *100 g* or *100 ml*, as printed on the package. Enter the values with up to two decimals; leave a value empty if the label doesn't give it (it stays unknown, never zero).
- **Package size (optional)**: e.g. 200 g, or 1 l for a liquid. When set, the diary also offers "1 × pakkaus" (or 0.5). Leave it empty for foods you measure, such as syrup. The package unit must match the nutrition basis - grams for per 100 g, ml/dl/l for per 100 ml - because grams and millilitres can't be converted without the food's density; the form only offers matching units.
- **Edit** a product to fix its values. Every recipe that uses it is recalculated automatically, and a message names them. Entries already in the diary keep their values.
- **Delete** removes the product. Diary entries keep their values. A product that is an ingredient of a recipe can't be deleted until it is removed from that recipe.

## Recipes (Nutrition → Recipes)

A recipe is a dish built from ingredients - Fineli foods and your own products or other recipes. Its values per 100 g are calculated from the ingredients.

### The Recipes page

Each recipe is a card with energy and macros per 100 g, the ingredient count and total mass, and - when set - servings and the size and energy of one serving. **Show details** expands the ingredient list and all values. Each card has **Open recipe**, **Duplicate** and **Delete**. Search appears when you have more than six recipes. **+ Create Recipe** asks for a name and opens the new recipe.

### The recipe page

From the top, built for adding many ingredients on a phone:

- **Ingredients** - each ingredient with its amount, kcal, carbohydrates (C) and protein (P) for that amount, and a total row. "≥" in a total means an ingredient doesn't report that value. Tap × twice to remove an ingredient.
- **Add from Fineli** and **Add from My Foods** - see below. After adding or removing, the page comes back to the same place with your search still there.
- **Recipe details** (folded) - name, servings, finished dish weight, Duplicate, Delete.
- **Nutrition** - choose **100 g**, **Serving** or **Recipe** (the whole recipe); the main values and **Other nutrients** follow the choice, and the page remembers it. *Serving* needs a servings count under Recipe details. **Correct values per 100 g** (folded) holds the manual correction form and **Recalculate from ingredients**.

### Building a recipe

1. **Add from Fineli** - start typing; suggestions appear as you type (best match first). With an empty search box, your starred Fineli favorites are listed for quick adding.
2. **Add from My Foods** - search your products and recipes; your most-used foods are shown first.
3. Tap a food, then set the amount in the panel that opens - grams, or a Fineli portion size with the resulting grams shown - and press **Add to recipe**.

Adding or removing an ingredient recalculates the recipe at once and confirms it with a message. A value stays empty (unknown) when any ingredient doesn't report it.

### Recipe details

- **Name** and **Brand / note**.
- **Servings** (optional) - how many servings the recipe makes. The diary can then log "1 × annos" as well as grams.
- **Finished dish weight** (optional) - weigh the cooked dish. Values per 100 g are then based on it, so water lost or gained in cooking is accounted for; otherwise the ingredients' total mass is used. Example: 200 g of oats cooked into 400 g of porridge gives 190 kcal per 100 g instead of 380.
- **Duplicate** copies the recipe (ingredients, servings, values) as a starting point for a variation.
- **Delete** (click twice) removes the recipe. Diary entries keep their values. A recipe used inside another recipe can't be deleted until it is removed there.

### Correcting values and recalculation

The stored values are per 100 g. **Correct values per 100 g** lets you fix them by hand; saving confirms with a message. Corrected values stay until the recipe's ingredients change. **Recalculate from ingredients** discards manual corrections and calculates the values again.

Recipes never go stale: when a product or recipe changes - edited, recalculated, or its final values saved - every recipe that uses it, also through another recipe, is recalculated, and a message names them.

## Favorites (Nutrition → Favorites)

Lists your starred Fineli foods and own foods; tap ★ to remove one. **Add favorites** searches Fineli and your own foods - tap ☆ to star. Favorites appear in the diary's ★ Favorites tab and at the top of a recipe's Fineli search.

## Diary CSV import

Imports food-level rows from Fineli diary CSV exports (from the Fineli website's diary).

1. Diary → **📥 Import CSV** (or the Diary CSV tab).
2. Select one or more CSV files and click **Import**.
3. The page refreshes when done.

Behavior:

- Each food row becomes a diary entry with calories, protein, carbs, fat, saturated fat, sugar, fiber, salt and the micronutrients, plus the original Fineli source details.
- Energy is converted from kJ to kcal. Below-detection-limit values (`<0.1`) are kept as such, not rounded to a plain number.
- The 5 standard Finnish meal names map to Breakfast/Lunch/Snack/Dinner/Supper (aamiainen, lounas, välipala, päivällinen, illallinen). Any other meal name (e.g. `Herkku`) gets its own section using the CSV's own name; a name that matches a standard meal's label (e.g. `Lounas`) is stored as written but shown in that standard meal's section.
- Re-importing a file adds only missing food rows and updates matching **CSV-imported** rows. Rows missing from a later file are kept, and entries you added by hand are never touched. Nothing is deleted.
- All selected files are validated before anything is saved. If one file is invalid, the whole upload is rejected. Each diary day is locked during the import so simultaneous uploads can't create duplicates. If existing duplicate CSV rows are found, the import stops without changing anything.

Import with your own account - entries belong to the user who imports them.

Endpoint: `POST /food-diary/api/bulk-import/` (login required).

## Troubleshooting

**Search returns nothing.** The Fineli table is empty - run `import_fineli_csv` (see [First-time setup](#first-time-setup)).

**Fineli calories look about 4× too high.** The Fineli database was imported before the 2026-10-11 kJ→kcal fix. Re-run the import with `--update-existing`. Entries logged before the fix keep their stored values.

**My entries disappeared.** Entries belong to the user who created them. Check which account you are logged in as (shown on the Log out button).

**Food logged just after midnight is on the previous day.** That happened before 2026-10-12, when the server used UTC. Delete the entry and add it again on the right day.

**"Give the package size in grams" error.** The product's values are per 100 g but the package size is in ml (or the other way round). Give the package in grams, switch the values to per 100 ml, or leave the package size empty.

**"… is an ingredient in …" when deleting.** Remove the food from the recipes named in the message first.

**A recipe value is empty.** One of its ingredients doesn't report that nutrient, so the total is unknown. Enter it by hand under Final nutrition values if you know it.

**Redirected to the admin login page.** Expected - the app uses Django admin's login. After logging in you land on the Dashboard; the admin site is only opened by going to `/admin/`.

## Data and privacy

- Everything is stored in the local SQLite database. Nothing is sent to Fineli or anywhere else.
- Day totals in the diary and on the dashboard count only your own entries, so another account logging or importing food on the same date can't change them.
- How the data is stored, backed up and checked: [database_guide.md](database_guide.md).

## Attribution

Food data: Finnish Institute for Health and Welfare (THL), Fineli® - https://fineli.fi/ - CC-BY 4.0.
