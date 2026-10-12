"""Pure, read-only daily aggregation for MealEntry nutrients - both the 8
typed core fields and auxiliary MealEntryNutrient values, behind one shared
DailyNutrientResult contract.

Source-agnostic: total_entry_count always includes every MealEntry on the
date regardless of source_type (bulk_import/manual/fineli/custom) - stored
None-vs-zero/qualifier values alone determine known/unknown/exact/lt.

Historical caveat: rows written before the None-vs-zero storage fix may still
contain fabricated zeros (a blank source value that was collapsed to 0).
Stored values are treated as authoritative here; this module cannot detect
or correct that legacy ambiguity.
"""
from dataclasses import dataclass

from django.db.models import Count, Q, Sum

from .models import MealEntry, MealEntryNutrient, NUTRIENT_UNITS

CORE_NUTRIENT_UNITS = {
    "calories": "kcal",
    "protein_g": "g",
    "carbs_g": "g",
    "fat_g": "g",
    "sat_fat_g": "g",
    "sugar_g": "g",
    "fiber_g": "g",
    "salt_g": "g",
}

# Core fields with a qualifier column. calories has none - every known
# calories value is exact (no "<x" ever proven in real source data).
CORE_QUALIFIER_FIELDS = {
    "protein_g": "protein_qualifier",
    "carbs_g": "carbs_qualifier",
    "fat_g": "fat_qualifier",
    "sat_fat_g": "sat_fat_qualifier",
    "sugar_g": "sugar_qualifier",
    "fiber_g": "fiber_qualifier",
    "salt_g": "salt_qualifier",
}


@dataclass(frozen=True)
class DailyNutrientResult:
    nutrient_key: str
    unit: str

    known_exact_sum: float
    known_upper_bound_sum: float  # upper bound of the KNOWN portion only

    known_entry_count: int
    total_entry_count: int

    coverage: str       # "no_meal_data" | "none" | "partial" | "complete"
    bound_status: str    # "exact" | "bounded"

    day_lower_bound: float
    day_upper_bound: float | None  # only set when coverage == "complete"


def daily_nutrient_result(date, nutrient_key: str) -> DailyNutrientResult:
    """Aggregate one nutrient across all MealEntry rows for `date`.

    `nutrient_key` is either one of the 8 typed core MealEntry field names
    (calories/protein_g/carbs_g/fat_g/sat_fat_g/sugar_g/fiber_g/salt_g) or an
    existing auxiliary MealEntryNutrient nutrient_key. Every MealEntry on the
    date is relevant - one with no known value for the requested nutrient
    counts as unknown, not zero, and is still included in total_entry_count.
    """
    if nutrient_key in CORE_NUTRIENT_UNITS:
        return _daily_core_nutrient_result(date, nutrient_key)
    if nutrient_key in NUTRIENT_UNITS:
        return _daily_auxiliary_nutrient_result(date, nutrient_key)
    raise ValueError(f"Unknown nutrient_key: '{nutrient_key}'")


def _daily_core_nutrient_result(date, field_name: str) -> DailyNutrientResult:
    total_entry_count = MealEntry.objects.filter(daily_log__date=date).count()

    known_rows = MealEntry.objects.filter(
        daily_log__date=date, **{f"{field_name}__isnull": False}
    )
    qualifier_field = CORE_QUALIFIER_FIELDS.get(field_name)
    if qualifier_field:
        rows = known_rows.values_list(field_name, qualifier_field)
    else:
        rows = ((value, "exact") for value in known_rows.values_list(field_name, flat=True))

    return _aggregate(field_name, CORE_NUTRIENT_UNITS[field_name], total_entry_count, rows)


def _daily_auxiliary_nutrient_result(date, nutrient_key: str) -> DailyNutrientResult:
    total_entry_count = MealEntry.objects.filter(daily_log__date=date).count()

    rows = MealEntryNutrient.objects.filter(
        meal_entry__daily_log__date=date, nutrient_key=nutrient_key
    ).values_list("value", "qualifier")

    return _aggregate(nutrient_key, NUTRIENT_UNITS[nutrient_key], total_entry_count, rows)


def _aggregate(nutrient_key: str, unit: str, total_entry_count: int, rows) -> DailyNutrientResult:
    known_exact_sum = 0.0
    known_upper_bound_sum = 0.0
    known_entry_count = 0
    has_lt = False

    for value, qualifier in rows:
        known_entry_count += 1
        known_upper_bound_sum += value
        if qualifier == "exact":
            known_exact_sum += value
        else:  # "lt"
            has_lt = True

    if total_entry_count == 0:
        coverage = "no_meal_data"
    elif known_entry_count == 0:
        coverage = "none"
    elif known_entry_count < total_entry_count:
        coverage = "partial"
    else:
        coverage = "complete"

    bound_status = "bounded" if has_lt else "exact"

    day_lower_bound = known_exact_sum
    day_upper_bound = known_upper_bound_sum if coverage == "complete" else None

    return DailyNutrientResult(
        nutrient_key=nutrient_key,
        unit=unit,
        known_exact_sum=known_exact_sum,
        known_upper_bound_sum=known_upper_bound_sum,
        known_entry_count=known_entry_count,
        total_entry_count=total_entry_count,
        coverage=coverage,
        bound_status=bound_status,
        day_lower_bound=day_lower_bound,
        day_upper_bound=day_upper_bound,
    )


@dataclass(frozen=True)
class MacroWindowResult:
    """Same unknown-vs-zero / partial-vs-complete philosophy as
    DailyNutrientResult, applied across a multi-day window in one bounded
    query set instead of once per day - for a weekly summary where calling
    daily_nutrient_result() per day/per-macro would multiply into a query
    explosion for no added truth."""
    known_sum: float
    known_entry_count: int
    total_entry_count: int
    coverage: str  # "no_data" | "none" | "partial" | "complete"


def weekly_macro_summary(user, start_date, end_date) -> dict[str, MacroWindowResult]:
    """Truthful protein_g/carbs_g/fat_g coverage across [start_date,
    end_date], at the MealEntry level. A macro is "complete" only if every
    MealEntry in the window has a known (non-null) value for it; otherwise
    "partial" (some known) or "none" (zero known, but entries exist).
    "no_data" means no MealEntry at all in the window. known_sum never
    substitutes 0 for an unknown value - Sum() already ignores NULLs.
    Only `user`'s own entries count - DailyLog rows are shared between users."""
    entries = MealEntry.objects.filter(user=user, daily_log__date__range=[start_date, end_date])
    agg = entries.aggregate(
        total_entries=Count("id"),
        protein_sum=Sum("protein_g"), protein_known=Count("id", filter=Q(protein_g__isnull=False)),
        carbs_sum=Sum("carbs_g"), carbs_known=Count("id", filter=Q(carbs_g__isnull=False)),
        fat_sum=Sum("fat_g"), fat_known=Count("id", filter=Q(fat_g__isnull=False)),
    )
    total = agg["total_entries"]

    def _result(sum_key, known_key):
        known_count = agg[known_key]
        if total == 0:
            coverage = "no_data"
        elif known_count == 0:
            coverage = "none"
        elif known_count < total:
            coverage = "partial"
        else:
            coverage = "complete"
        return MacroWindowResult(
            known_sum=agg[sum_key] or 0.0, known_entry_count=known_count,
            total_entry_count=total, coverage=coverage,
        )

    return {
        "protein_g": _result("protein_sum", "protein_known"),
        "carbs_g": _result("carbs_sum", "carbs_known"),
        "fat_g": _result("fat_sum", "fat_known"),
    }


def logged_nutrition_days_count(user, start_date, end_date) -> int:
    """Distinct calendar dates in [start_date, end_date] with at least one of
    `user`'s MealEntry rows - the truthful denominator for a weekly nutrition
    average. A date with zero MealEntry rows is unknown/unlogged, never a
    0-intake day."""
    return (
        MealEntry.objects.filter(user=user, daily_log__date__range=[start_date, end_date])
        .values("daily_log__date").distinct().count()
    )


def user_daily_totals(user, start_date, end_date) -> dict:
    """{date: {"calories_in", "protein_g", ..., "salt_g"}} summed from
    `user`'s own MealEntry rows in [start_date, end_date], in one grouped
    query. DailyLog is one shared row per date (it also carries the Garmin
    data), so its stored nutrition totals add up every user's entries - read
    per-user intake from here instead. Dates without entries are absent.
    Unknown (None) values are skipped, like DailyLog.recalculate_from_meals."""
    rows = (
        MealEntry.objects.filter(user=user, daily_log__date__range=[start_date, end_date])
        .values("daily_log__date")
        .annotate(
            calories=Sum("calories"),
            protein=Sum("protein_g"),
            carbs=Sum("carbs_g"),
            fat=Sum("fat_g"),
            sat_fat=Sum("sat_fat_g"),
            sugar=Sum("sugar_g"),
            fiber=Sum("fiber_g"),
            salt=Sum("salt_g"),
        )
    )
    return {
        row["daily_log__date"]: {
            "calories_in": int(row["calories"] or 0),
            "protein_g": float(row["protein"] or 0),
            "carbs_g": float(row["carbs"] or 0),
            "fat_g": float(row["fat"] or 0),
            "sat_fat_g": float(row["sat_fat"] or 0),
            "sugar_g": float(row["sugar"] or 0),
            "fiber_g": float(row["fiber"] or 0),
            "salt_g": float(row["salt"] or 0),
        }
        for row in rows
    }
