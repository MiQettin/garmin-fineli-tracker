"""Pure, read-only recipe-composition aggregation for CustomFoodIngredient -
both the 8 typed core fields and auxiliary CustomFoodNutrient/
FineliFoodNutrient values, behind one RecipeNutrientResult contract that
mirrors aggregation.py's DailyNutrientResult semantics (same coverage/bound
philosophy, applied to a recipe's ingredients instead of a date's meal
entries).

Nested source_custom_food ingredients are NOT recursively expanded here -
they contribute their own currently persisted per-100g values directly, not
their own ingredients' composition. CustomFoodIngredient.clean() still
enforces cycle-freedom so a later recursive-recalculation slice stays safe.

Never mutates FineliFood/FineliFoodNutrient/CustomFood/CustomFoodNutrient or
writes back into the parent CustomFood - read-only by design.
"""
from dataclasses import dataclass

from .models import CustomFoodNutrient, FineliFoodNutrient, NUTRIENT_UNITS

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

# Result core field name -> (FineliFood field, CustomFood field)
_CORE_FIELD_MAP = {
    "calories": ("energy_kcal", "energy_kcal_per_100g"),
    "protein_g": ("protein_g", "protein_per_100g"),
    "carbs_g": ("carbs_g", "carbs_per_100g"),
    "sugar_g": ("sugar_g", "sugar_per_100g"),
    "fat_g": ("fat_g", "fat_per_100g"),
    "sat_fat_g": ("sat_fat_g", "sat_fat_per_100g"),
    "fiber_g": ("fiber_g", "fiber_per_100g"),
    "salt_g": ("salt_g", "salt_per_100g"),
}


@dataclass(frozen=True)
class RecipeNutrientResult:
    nutrient_key: str
    unit: str

    known_exact_sum: float
    known_upper_bound_sum: float  # upper bound of the KNOWN portion only

    known_ingredient_count: int
    total_ingredient_count: int

    coverage: str       # "no_ingredient_data" | "none" | "partial" | "complete"
    bound_status: str    # "exact" | "bounded"

    recipe_lower_bound: float
    recipe_upper_bound: float | None  # only set when coverage == "complete"


@dataclass(frozen=True)
class Per100gNutrientResult:
    nutrient_key: str
    unit: str
    lower_bound: float | None
    upper_bound: float | None
    coverage: str
    bound_status: str


def total_recipe_grams(custom_food) -> float:
    """Sum of this recipe's ingredient amounts. 0 for an empty recipe."""
    return sum(ing.amount_grams for ing in custom_food.ingredients.all())


def recipe_nutrient_result(custom_food, nutrient_key: str) -> RecipeNutrientResult:
    """Aggregate one nutrient across all of custom_food's ingredients.

    `nutrient_key` is either one of the 8 typed core field names or an
    existing auxiliary nutrient_key. Every ingredient is relevant - one with
    no known value for the requested nutrient counts as unknown, not zero,
    and is still included in total_ingredient_count.
    """
    if nutrient_key in CORE_NUTRIENT_UNITS:
        return _recipe_core_nutrient_result(custom_food, nutrient_key)
    if nutrient_key in NUTRIENT_UNITS:
        return _recipe_auxiliary_nutrient_result(custom_food, nutrient_key)
    raise ValueError(f"Unknown nutrient_key: '{nutrient_key}'")


def _recipe_core_nutrient_result(custom_food, field_name: str) -> RecipeNutrientResult:
    fineli_field, custom_field = _CORE_FIELD_MAP[field_name]
    ingredients = list(custom_food.ingredients.select_related('fineli_food', 'source_custom_food'))

    rows = []
    for ing in ingredients:
        if ing.fineli_food_id:
            raw_value = getattr(ing.fineli_food, fineli_field)
        else:
            raw_value = getattr(ing.source_custom_food, custom_field)
        if raw_value is None:
            continue
        rows.append((raw_value * ing.amount_grams / 100.0, "exact"))

    return _aggregate(field_name, CORE_NUTRIENT_UNITS[field_name], len(ingredients), rows)


def _recipe_auxiliary_nutrient_result(custom_food, nutrient_key: str) -> RecipeNutrientResult:
    ingredients = list(custom_food.ingredients.all())
    fineli_ids = [ing.fineli_food_id for ing in ingredients if ing.fineli_food_id]
    custom_ids = [ing.source_custom_food_id for ing in ingredients if ing.source_custom_food_id]

    fineli_rows = {
        fid: (value, qualifier)
        for fid, value, qualifier in FineliFoodNutrient.objects.filter(
            fineli_food_id__in=fineli_ids, nutrient_key=nutrient_key
        ).values_list('fineli_food_id', 'value', 'qualifier')
    }
    custom_rows = {
        cid: (value, qualifier)
        for cid, value, qualifier in CustomFoodNutrient.objects.filter(
            custom_food_id__in=custom_ids, nutrient_key=nutrient_key
        ).values_list('custom_food_id', 'value', 'qualifier')
    }

    rows = []
    for ing in ingredients:
        source = fineli_rows.get(ing.fineli_food_id) if ing.fineli_food_id else custom_rows.get(ing.source_custom_food_id)
        if source is None:
            continue
        value, qualifier = source
        rows.append((value * ing.amount_grams / 100.0, qualifier))

    return _aggregate(nutrient_key, NUTRIENT_UNITS[nutrient_key], len(ingredients), rows)


def _aggregate(nutrient_key: str, unit: str, total_ingredient_count: int, rows) -> RecipeNutrientResult:
    known_exact_sum = 0.0
    known_upper_bound_sum = 0.0
    known_ingredient_count = 0
    has_lt = False

    for value, qualifier in rows:
        known_ingredient_count += 1
        known_upper_bound_sum += value
        if qualifier == "exact":
            known_exact_sum += value
        else:  # "lt"
            has_lt = True

    if total_ingredient_count == 0:
        coverage = "no_ingredient_data"
    elif known_ingredient_count == 0:
        coverage = "none"
    elif known_ingredient_count < total_ingredient_count:
        coverage = "partial"
    else:
        coverage = "complete"

    bound_status = "bounded" if has_lt else "exact"

    recipe_lower_bound = known_exact_sum
    recipe_upper_bound = known_upper_bound_sum if coverage == "complete" else None

    return RecipeNutrientResult(
        nutrient_key=nutrient_key,
        unit=unit,
        known_exact_sum=known_exact_sum,
        known_upper_bound_sum=known_upper_bound_sum,
        known_ingredient_count=known_ingredient_count,
        total_ingredient_count=total_ingredient_count,
        coverage=coverage,
        bound_status=bound_status,
        recipe_lower_bound=recipe_lower_bound,
        recipe_upper_bound=recipe_upper_bound,
    )


def normalize_to_per_100g(result: RecipeNutrientResult, total_grams: float) -> Per100gNutrientResult:
    """Pure per-100g rescaling of an already-computed RecipeNutrientResult.
    Never changes coverage/bound_status - only the numeric bounds. A
    non-positive total_grams (empty recipe) yields no calculable value
    (None), never a fabricated zero."""
    if total_grams and total_grams > 0:
        factor = 100.0 / total_grams
        lower = result.recipe_lower_bound * factor
        upper = result.recipe_upper_bound * factor if result.recipe_upper_bound is not None else None
    else:
        lower = None
        upper = None

    return Per100gNutrientResult(
        nutrient_key=result.nutrient_key,
        unit=result.unit,
        lower_bound=lower,
        upper_bound=upper,
        coverage=result.coverage,
        bound_status=result.bound_status,
    )
