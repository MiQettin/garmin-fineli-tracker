"""Utility functions for nutrition app."""
import csv
import io

from .models import NUTRIENT_UNITS


def _to_float(x) -> float:
    """Convert value to float, handling Finnish CSV formats."""
    if x is None:
        return 0.0
    s = str(x).strip()
    if not s:
        return 0.0

    s = s.replace(" ", "").replace(",", ".")
    if s.startswith("<"):
        s = s[1:]  # "<0.1" -> "0.1"

    if s.lower() in ("tr", "trace", "-", "nan"):
        return 0.0

    try:
        return float(s)
    except ValueError:
        return 0.0


def _to_qualified_value(x) -> tuple[float, str] | None:
    """Parse an auxiliary nutrient source cell into (value, qualifier).

    The Fineli diary CSV reports some values as a below-detection-limit
    threshold ("<0.1") rather than an exact number. This preserves that
    distinction instead of collapsing it into the plain numeric threshold.
    Returns None for blank (unknown, no row should be created).
    """
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None

    s = s.replace(" ", "").replace(",", ".")
    qualifier = "exact"
    if s.startswith("<"):
        qualifier = "lt"
        s = s[1:]

    if s.lower() in ("tr", "trace", "-", "nan"):
        return (0.0, qualifier)

    try:
        return (float(s), qualifier)
    except ValueError:
        return None


def _to_qualified_core_value(x) -> tuple[float | None, str]:
    """Like _to_float but also returns the source qualifier ('exact' or
    'lt' for a below-detection-limit '<x' value), for the typed core
    MealEntry nutrient fields proven to carry '<x' in the real diary CSV.

    Blank/unparseable source cells return (None, "exact") - unknown/not
    supplied, never a fabricated zero. An explicit "0" still returns
    (0.0, "exact"). The qualifier is meaningless when value is None; callers
    must treat a None value as unknown regardless of the qualifier."""
    if x is None:
        return (None, "exact")
    s = str(x).strip()
    if not s:
        return (None, "exact")

    s = s.replace(" ", "").replace(",", ".")
    qualifier = "exact"
    if s.startswith("<"):
        qualifier = "lt"
        s = s[1:]

    if s.lower() in ("tr", "trace", "-", "nan"):
        return (0.0, qualifier)

    try:
        return (float(s), qualifier)
    except ValueError:
        return (None, qualifier)


def _to_optional_float(x) -> float | None:
    """Like _to_float but returns None for blank instead of 0.0, so an
    explicit source 0 stays distinguishable from unknown/blank."""
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    s = s.replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


# Maps the diary's 5 standard Finnish meal-slot names onto our canonical
# internal keys, so they group under the diary page's regular Breakfast/
# Lunch/Dinner/Snack/Evening Snack sections. Fineli also lets users rename
# or add meal slots freely (e.g. "Herkku", "Iltapala", or a custom name) -
# a name not in this map is not forced into one of the 5 buckets; the raw
# CSV text is used as the meal_type verbatim and gets its own section on
# the diary page.
MEAL_TYPE_MAP = {
    "aamiainen": "breakfast",
    "lounas": "lunch",
    "välipala": "snack",
    "päivällinen": "dinner",
    "illallinen": "evening_snack",
}


# Real Fineli diary CSV header (normalized: stripped, BOM-free, lowercased) ->
# canonical MealEntryNutrient nutrient_key. Units are looked up from the
# single source of truth NUTRIENT_UNITS, never duplicated here.
AUX_NUTRIENT_HEADERS = {
    "alkoholi (g)": "alcohol",
    "orgaaniset hapot (g)": "organic_acids",
    "sokerialkoholi (g)": "sugar_alcohols",
    "fruktoosi (g)": "fructose",
    "galaktoosi (g)": "galactose",
    "glukoosi (g)": "glucose",
    "laktoosi (g)": "lactose",
    "maltoosi (g)": "maltose",
    "sakkaroosi (g)": "sucrose",
    "tärkkelys (g)": "starch",
    "kuitu veteen liukenematon (g)": "insoluble_fiber",
    "polysakkaridi, vesiliukoinen ei-selluloosa (g)": "soluble_noncellulose_polysaccharide",
    "rasvahapot yhteensä (g)": "fatty_acids_total",
    "rasvahapot monityydyttymättömät (g)": "pufa",
    "rasvahapot yksittäistyydyttymättömät cis (g)": "mufa_cis",
    "rasvahapot trans (g)": "trans_fat",
    "rasvahapot n-3 monityydyttymättömät (g)": "omega3",
    "rasvahapot n-6 monityydyttymättömät (g)": "omega6",
    "rasvahappo 18:2 cis,cis n-6 (linolihappo) (mg)": "linoleic_acid",
    "rasvahappo 18:3 n-3 (alfalinoleenihappo) (mg)": "ala",
    "rasvahappo 20:5 n-3 (epa) (mg)": "epa",
    "rasvahappo 22:6 n-3 (dha) (mg)": "dha",
    "kolesteroli (gc) (mg)": "cholesterol",
    "sterolit (mg)": "sterols",
    "kalsium (mg)": "calcium",
    "rauta (mg)": "iron",
    "jodidi (jodi) (µg)": "iodine",
    "kalium (mg)": "potassium",
    "magnesium (mg)": "magnesium",
    "natrium (mg)": "sodium",
    "fosfori (mg)": "phosphorus",
    "seleeni (µg)": "selenium",
    "sinkki (mg)": "zinc",
    "tryptofaani (mg)": "tryptophan",
    "folaatti, kokonais- (µg)": "folate",
    "niasiiniekvivalentti ne (mg)": "niacin_equivalent",
    "niasiini (nikotiinihappo + nikotiiniamidi) (mg)": "niacin",
    "pyridoksiini vitameerit (vetykloridi) (b6) (mg)": "vitamin_b6",
    "riboflaviini (b2) (mg)": "riboflavin",
    "tiamiini (b1) (mg)": "thiamine",
    "a-vitamiini rae (µg)": "vitamin_a_rae",
    "karotenoidit (µg)": "carotenoids",
    "b12-vitamiini (kobalamiini) (µg)": "vitamin_b12",
    "c-vitamiini (mg)": "vitamin_c",
    "d-vitamiini (µg)": "vitamin_d",
    "e-vitamiini alfatokoferoli (mg)": "vitamin_e",
    "k-vitamiini (µg)": "vitamin_k",
}


def parse_food_rows_from_content(content: str) -> dict[str, list[dict]]:
    """
    Reads diary CSV from string content and returns individual food-level
    rows (not meal subtotals, not daily totals) for each date, ready to be
    stored as MealEntry records.

    A food row is identified by having both 'meal' and 'food' populated.
    Meal-subtotal rows (meal populated, food empty) and daily-total rows
    (both empty) are skipped.

    Returns:
        dict mapping date string (YYYY-MM-DD) -> list of dicts with keys:
        food_name, meal_type, portion_grams, calories, protein_g, carbs_g,
        fat_g, sat_fat_g, sugar_g, fiber_g, salt_g, source_food_id,
        source_amount, source_unit, source_ingredients_classification,
        source_food_use_classification, protein_qualifier, carbs_qualifier,
        fat_qualifier, sat_fat_qualifier, sugar_qualifier, fiber_qualifier,
        salt_qualifier ('exact' or 'lt' for a below-detection-limit source
        value like '<0.1'; calories has no qualifier - real data proves the
        energy column is always exact), aux_nutrients (list of
        {nutrient_key, value, qualifier, unit} for whichever auxiliary
        columns are present and non-blank on that row; qualifier is 'exact'
        or 'lt' for a below-detection-limit source value like '<0.1')
    """
    sample = content[:4096]
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","

    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    headers = list(reader.fieldnames or [])

    def norm(h: str) -> str:
        return (h or "").strip().lstrip("﻿").lower()

    H = {norm(h): h for h in headers}

    def require(name: str) -> str:
        key = norm(name)
        if key not in H:
            raise ValueError(
                f"Missing required column: '{name}'. "
                f"Available columns: {', '.join(headers[:5])}{'...' if len(headers) > 5 else ''}"
            )
        return H[key]

    h_date = require("date")
    h_meal = require("meal")
    h_food = require("food")
    h_mass = require("mass (g)")
    h_kj = require("energia, laskennallinen (kJ)")
    h_fat = require("rasva (g)")
    h_carbs = require("hiilihydraatti imeytyvä (g)")
    h_prot = require("proteiini (g)")
    h_sat = require("rasvahapot tyydyttyneet (g)")
    h_sugar = require("sokerit (g)")
    h_fiber = require("kuitu, kokonais- (g)")
    h_salt = require("suola (mg)")

    # Original source provenance columns are optional (older exports/test
    # fixtures may omit them) - absence means unknown, not zero.
    h_food_id = H.get(norm("foodid"))
    h_amount = H.get(norm("amount"))
    h_unit = H.get(norm("unit"))
    h_ingredients_class = H.get(norm("ingredients (classification)"))
    h_food_use_class = H.get(norm("food use (classification)"))

    # Auxiliary nutrient columns are optional: resolve whichever of the known
    # headers are present in this file, skip the rest silently.
    h_aux = {
        nutrient_key: H[header]
        for header, nutrient_key in AUX_NUTRIENT_HEADERS.items()
        if header in H
    }

    result: dict[str, list[dict]] = {}

    for row in reader:
        date_str = (row.get(h_date) or "").strip()
        meal = (row.get(h_meal) or "").strip()
        food_name = (row.get(h_food) or "").strip()

        if not date_str or not meal or not food_name:
            continue  # meal-subtotal or daily-total row

        meal_type = MEAL_TYPE_MAP.get(meal.lower(), meal)

        aux_nutrients = []
        for nutrient_key, header in h_aux.items():
            raw = row.get(header)
            if raw is None or not raw.strip():
                continue  # blank/unavailable -> no row (unknown, not zero)
            parsed = _to_qualified_value(raw)
            if parsed is None:
                continue
            value, qualifier = parsed
            aux_nutrients.append({
                "nutrient_key": nutrient_key,
                "value": value,
                "qualifier": qualifier,
                "unit": NUTRIENT_UNITS[nutrient_key],
            })

        source_food_id_val = _to_optional_float(row.get(h_food_id) if h_food_id else None)
        source_food_id = int(source_food_id_val) if source_food_id_val is not None else None
        source_amount = _to_optional_float(row.get(h_amount) if h_amount else None)
        source_unit_raw = (row.get(h_unit) if h_unit else None) or ""
        source_unit = source_unit_raw.strip()
        source_ingredients_classification = (
            (row.get(h_ingredients_class) if h_ingredients_class else None) or ""
        ).strip()
        source_food_use_classification = (
            (row.get(h_food_use_class) if h_food_use_class else None) or ""
        ).strip()

        protein_g, protein_qualifier = _to_qualified_core_value(row.get(h_prot))
        carbs_g, carbs_qualifier = _to_qualified_core_value(row.get(h_carbs))
        fat_g, fat_qualifier = _to_qualified_core_value(row.get(h_fat))
        sat_fat_g, sat_fat_qualifier = _to_qualified_core_value(row.get(h_sat))
        sugar_g, sugar_qualifier = _to_qualified_core_value(row.get(h_sugar))
        fiber_g, fiber_qualifier = _to_qualified_core_value(row.get(h_fiber))
        salt_val, salt_qualifier = _to_qualified_core_value(row.get(h_salt))
        salt_g = salt_val / 1000.0 if salt_val is not None else None
        calories_val, _ = _to_qualified_core_value(row.get(h_kj))
        calories = calories_val / 4.184 if calories_val is not None else None

        result.setdefault(date_str, []).append({
            "food_name": food_name,
            "meal_type": meal_type,
            "portion_grams": _to_float(row.get(h_mass)),
            "source_food_id": source_food_id,
            "source_amount": source_amount,
            "source_unit": source_unit,
            "source_ingredients_classification": source_ingredients_classification,
            "source_food_use_classification": source_food_use_classification,
            "calories": calories,
            "protein_g": protein_g,
            "protein_qualifier": protein_qualifier,
            "carbs_g": carbs_g,
            "carbs_qualifier": carbs_qualifier,
            "fat_g": fat_g,
            "fat_qualifier": fat_qualifier,
            "sat_fat_g": sat_fat_g,
            "sat_fat_qualifier": sat_fat_qualifier,
            "sugar_g": sugar_g,
            "sugar_qualifier": sugar_qualifier,
            "fiber_g": fiber_g,
            "fiber_qualifier": fiber_qualifier,
            "salt_g": salt_g,
            "salt_qualifier": salt_qualifier,
            "aux_nutrients": aux_nutrients,
        })

    return result
