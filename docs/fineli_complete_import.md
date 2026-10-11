# Complete Fineli Database Import Guide

Last updated: 2026-10-11

## Purpose

The food diary searches only the local database - there is no live Fineli API lookup. **This import is required**: until it has run, Fineli search returns no results.

The current import command expects an extracted Fineli release folder, not one standalone CSV file.

## Command

```bash
cd backend
python manage.py import_fineli_csv --data-folder "C:\path\to\Fineli_Rel20"
```

Update existing foods:

```bash
python manage.py import_fineli_csv --data-folder "C:\path\to\Fineli_Rel20" --update-existing
```

`--csv-path` still exists in the command parser only as a deprecated argument. Do not use it for current imports.

## Download Fineli Open Data

1. Open https://fineli.fi/fineli/en/avoin-data
2. Download the current Basic Package (the 74-nutrient package is the most complete).
3. Extract the ZIP.
4. Point `--data-folder` at the extracted folder.

The folder should contain files like:

- `foodname_FI.csv`
- `foodname_SV.csv`
- `foodname_EN.csv`
- `component_value.csv`

## What Gets Imported

For each food, the importer combines multilingual food names with nutrient values.

Mapped nutrients:

| Fineli code | HealthManager field |
| --- | --- |
| `ENERC` | `energy_kcal` (Fineli reports kJ; converted / 4.184) |
| `PROT` | `protein_g` |
| `CHOAVL` | `carbs_g` |
| `SUGAR` | `sugar_g` |
| `FAT` | `fat_g` |
| `FASAT` | `sat_fat_g` |
| `FIBC` | `fiber_g` |
| `NACL` | `salt_g` (Fineli reports mg; converted to g) |

Values are stored per 100g in `nutrition.models.FineliFood`. A nutrient Fineli doesn't report for a food is stored as unknown (`None`), never as 0. Auxiliary micronutrients are stored as `FineliFoodNutrient` rows.

The import is idempotent: re-running with `--update-existing` refreshes every food in place.

## Verify Import

```bash
cd backend
python manage.py shell -c "from nutrition.models import FineliFood; print(FineliFood.objects.count())"
```

Search for a known food:

```bash
python manage.py shell -c "from nutrition.models import FineliFood; qs = FineliFood.objects.filter(name_fi__icontains='mustikka'); print(qs.count()); [print(f'{f.fineli_id}: {f.name_fi}') for f in qs[:10]]"
```

Expected count is roughly 4,200 foods, depending on the Fineli release.

## Troubleshooting

### `--data-folder is required`

Pass the extracted folder:

```bash
python manage.py import_fineli_csv --data-folder "C:\path\to\Fineli_Rel20"
```

### `component_value.csv` not found

The path points to the wrong folder. Use the folder that directly contains `component_value.csv`.

### Names are missing

Check that at least one of these files exists in the folder:

- `foodname_FI.csv`
- `foodname_SV.csv`
- `foodname_EN.csv`

### Values look wrong

If energy values look about 4x too high (e.g. oat flakes around 1600 instead of about 380 kcal), the data was imported before the 2026-10-11 kJ->kcal fix - re-run with `--update-existing`.


Fineli CSV files use semicolon delimiters and locale decimal commas. The importer handles this for `component_value.csv`; if a future Fineli release changes column names, update `import_fineli_csv.py`.

## Attribution

When using Fineli data, credit:

Finnish Institute for Health and Welfare (THL), Fineli

License: Creative Commons 4.0 Attribution (CC-BY).
