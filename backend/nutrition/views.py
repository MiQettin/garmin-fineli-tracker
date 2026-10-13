from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils.timezone import localdate, now
from django.http import JsonResponse
from django.db import transaction
from django.db.models import Q
import json
from urllib.parse import urlencode

from core.models import DailyLog
from .models import (
    FineliFood, CustomFood, CustomFoodIngredient, CustomFoodNutrient, FavoriteFood, MealEntry, MealEntryNutrient,
    fineli_unit_sort_key,
)
from .forms import (
    NUTRIENT_ENTRY_FIELDS, CustomFoodForm, FinalNutrientValuesForm, MealEntryEditForm, MealEntryForm,
    RecipeCreateForm, RecipeSettingsForm,
)
from .recipe_aggregation import recipe_yield_grams, serving_grams, total_recipe_grams
from .recipe_persistence import (
    RecipeGraphError, recalculate_custom_food_recipe_with_dependencies, recalculate_dependent_recipes,
)


@login_required
def food_diary_view(request):
    """View for managing daily food diary."""
    date_str = request.GET.get('date')
    if date_str:
        from datetime import datetime
        current_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    else:
        current_date = localdate()
    
    daily_log, created = DailyLog.objects.get_or_create(date=current_date)
    meal_entries = daily_log.meal_entries.filter(user=request.user).order_by('meal_type', '-created_at')
    custom_foods = CustomFood.objects.filter(user=request.user)[:20]

    # The 5 standard sections are always shown (so there's always somewhere
    # to manually add food from a fresh day). A bulk CSV import can also
    # bring in a diary meal-slot name outside this set (e.g. "Herkku") -
    # those get their own section, appended after the standard ones, named
    # verbatim after the CSV's own text rather than forced into one of the 5.
    base_meal_types = [
        ('breakfast', '🌅 Breakfast'),
        ('lunch', '🌞 Lunch'),
        ('dinner', '🌙 Dinner'),
        ('snack', '🍎 Snack'),
        ('evening_snack', '🌜 Evening Snack'),
    ]
    base_meal_keys = {key for key, _ in base_meal_types}
    extra_meal_names = sorted({
        entry.meal_type for entry in meal_entries if entry.meal_type not in base_meal_keys
    })
    meal_types = base_meal_types + [(name, f'🍽️ {name}') for name in extra_meal_names]

    meal_totals = {
        key: {'calories': 0, 'protein': 0, 'carbs': 0, 'sugar': 0, 'fat': 0, 'sat_fat': 0, 'fiber': 0, 'salt': 0}
        for key, _ in meal_types
    }

    # Unknown (None) values are skipped, like DailyLog.recalculate_from_meals'
    # Sum() - some Fineli foods lack e.g. sugar or fiber.
    for entry in meal_entries:
        totals = meal_totals[entry.meal_type]
        totals['calories'] += entry.calories or 0
        totals['protein'] += entry.protein_g or 0
        totals['carbs'] += entry.carbs_g or 0
        totals['sugar'] += entry.sugar_g or 0
        totals['fat'] += entry.fat_g or 0
        totals['sat_fat'] += entry.sat_fat_g or 0
        totals['fiber'] += entry.fiber_g or 0
        totals['salt'] += entry.salt_g or 0

    # Day summary from this user's own entries - daily_log's stored totals
    # are shared between users and would include everyone's food.
    day_totals = {
        f'{key}_in' if key == 'calories' else f'{key}_g': sum(t[key] for t in meal_totals.values())
        for key in ('calories', 'protein', 'carbs', 'sugar', 'fat', 'sat_fat', 'fiber', 'salt')
    }

    prev_date = current_date - timedelta(days=1)
    next_date = current_date + timedelta(days=1)

    context = {
        'current_date': current_date,
        'daily_log': daily_log,
        'meal_entries': meal_entries,
        'custom_foods': custom_foods,
        'meal_totals': meal_totals,
        'day_totals': day_totals,
        'meal_types': meal_types,
        'prev_date': prev_date,
        'next_date': next_date,
        'is_today': current_date == localdate(),
    }
    
    return render(request, "nutrition/food_diary.html", context)


@login_required
def add_custom_food(request):
    """Add a new custom food, owned by request.user. Ownership never comes
    from form/client input - CustomFoodForm has no user field."""
    if request.method == "POST":
        form = CustomFoodForm(request.POST)
        if form.is_valid():
            food = form.save(commit=False)
            food.user = request.user
            food.save()
            return redirect('manage_custom_foods')
    else:
        form = CustomFoodForm()

    # There is no separate add page - show the form (with any errors) on the
    # custom foods page, which is where the product form lives.
    return render(request, "nutrition/manage_custom_foods.html", {
        "custom_foods": CustomFood.objects.filter(user=request.user, kind=CustomFood.KIND_PRODUCT),
        "form": form,
    })


@login_required
def create_recipe(request):
    """Create the identifying shell of a new recipe (name/brand only - no
    fabricated nutrient values) and go straight to its ingredient/recipe
    composition page, rather than making the user pass through the manual
    product form first."""
    if request.method == "POST":
        form = RecipeCreateForm(request.POST)
        if form.is_valid():
            recipe = form.save(commit=False)
            recipe.user = request.user
            recipe.kind = CustomFood.KIND_RECIPE
            recipe.save()
            return redirect('recipe_detail', food_id=recipe.id)
    else:
        form = RecipeCreateForm()

    return render(request, "nutrition/create_recipe.html", {"form": form})


@login_required
def recipe_list(request):
    """The user's saved recipes as cards."""
    recipes = list(
        CustomFood.objects.filter(user=request.user, kind=CustomFood.KIND_RECIPE)
        .prefetch_related('ingredients__fineli_food', 'ingredients__source_custom_food')
        .order_by('name')
    )
    for food in recipes:
        food.ingredient_list = list(food.ingredients.all())
        food.total_grams = sum(i.amount_grams or 0 for i in food.ingredient_list)
        food.serving_g = serving_grams(food)
        food.serving_kcal = (
            food.energy_kcal_per_100g * food.serving_g / 100
            if food.serving_g and food.energy_kcal_per_100g is not None else None
        )
    return render(request, "nutrition/recipes.html", {
        'recipes': recipes,
        'form': RecipeCreateForm(),
    })


@login_required
def manage_custom_foods(request):
    """Manage the current user's own products (recipes have their own page)."""
    custom_foods = CustomFood.objects.filter(user=request.user, kind=CustomFood.KIND_PRODUCT)

    if request.method == "POST":
        form = CustomFoodForm(request.POST)
        if form.is_valid():
            food = form.save(commit=False)
            food.user = request.user
            food.save()
            return redirect('manage_custom_foods')
    else:
        form = CustomFoodForm()

    context = {
        'custom_foods': custom_foods,
        'form': form,
    }

    return render(request, "nutrition/manage_custom_foods.html", context)


@login_required
def edit_custom_food(request, food_id):
    """Edit an existing custom food. Scoped to request.user so another
    user's (or a legacy unowned) custom food cannot be read or mutated."""
    food = get_object_or_404(CustomFood, pk=food_id, user=request.user)

    if request.method == "POST":
        form = CustomFoodForm(request.POST, instance=food)
        if form.is_valid():
            food = form.save()
            messages.success(request, f"Saved {food.name}.")
            _update_dependent_recipes(request, food)
            return redirect('manage_custom_foods')
    else:
        form = CustomFoodForm(instance=food)

    return render(request, "nutrition/edit_custom_food.html", {"form": form, "food": food})


@login_required
def delete_custom_food(request, food_id):
    """Delete a custom food. Scoped to request.user so another user's (or a
    legacy unowned) custom food cannot be deleted."""
    food = get_object_or_404(CustomFood, pk=food_id, user=request.user)
    if request.method == "POST":
        used_in = _recipes_using(food)
        if used_in:
            messages.error(request, f"{food.name} is an ingredient in {used_in} - remove it there first.")
        else:
            food.delete()
            messages.success(request, f"Deleted {food.name}.")
    return redirect('manage_custom_foods')


def search_words(query):
    """The words of a search query ("maito, lak" -> ["maito", "lak"])."""
    return query.replace(",", " ").split()


def name_search(fields, query):
    """Case-insensitive name search that also works for ä/ö/å.

    Every word of the query must appear (in any order and any of `fields`),
    so "maito lak" finds "MAITO, KEVYTMAITO, LAKTOOSITON".

    SQLite's LIKE folds case for ASCII letters only, so "vehnäjauho" never
    matched Fineli's upper-case "VEHNÄJAUHO". Matching each word as typed,
    upper-, lower- and capitalised covers Fineli's all-caps names and normal
    mixed-case custom food names."""
    condition = Q()
    for word in search_words(query):
        variants = {word, word.upper(), word.lower(), word.capitalize()}
        word_matches = Q()
        for field in fields:
            for variant in variants:
                word_matches |= Q(**{f"{field}__icontains": variant})
        condition &= word_matches
    return condition


def _match_rank(name, query):
    """Relevance of a Fineli name for `query`, lower is better. Fineli names
    are comma-separated, most general part first ("BANAANI, KUORITTU",
    "JAUHO, VEHNÄJAUHO, ..."), so where the query appears matters:
    0 first part is the query, 1 first part starts with it, 2 second part is
    the query, 3 a later part is, 4 a part starts with it, 5 anywhere else."""
    query = query.casefold()
    parts = [part.strip() for part in (name or "").casefold().split(",")]
    if parts[0] == query:
        return 0
    if parts[0].startswith(query):
        return 1
    if len(parts) > 1 and parts[1] == query:
        return 2
    if query in parts:
        return 3
    if any(part.startswith(query) for part in parts):
        return 4
    return 5


def ranked_fineli_search(query, fields=('name_fi', 'name_sv', 'name_en'), limit=20, by_usage=False):
    """FineliFood matches for `query`, best matches first (see
    _match_rank), optionally most-used first. Ranks on the lightweight
    values, then loads only the top `limit` rows with their units."""
    rows = FineliFood.objects.filter(name_search(fields, query)).values_list(
        'fineli_id', 'name_fi', 'usage_count',
    )
    words = search_words(query)
    # The first word decides most (it's usually the food itself: "maito"
    # in "maito lak"), the rest break ties; then shorter = more generic.
    ordered = sorted(rows, key=lambda r: (
        -(r[2] or 0) if by_usage else 0,
        _match_rank(r[1], words[0]),
        sum(_match_rank(r[1], word) for word in words[1:]),
        len(r[1] or ""),
        r[1] or "",
    ))
    top_ids = [r[0] for r in ordered[:limit]]
    foods = FineliFood.objects.filter(fineli_id__in=top_ids).prefetch_related('units').in_bulk(field_name='fineli_id')
    return [foods[i] for i in top_ids]


def _parse_grams(raw):
    """float(raw) or None for blank/invalid - clean()'s own
    'amount_grams is None or <= 0' check turns that into a user-facing
    validation error rather than a 500 here."""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _validation_error_message(exc: ValidationError) -> str:
    return "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)


def _format_nutrient_value(value):
    """Keep ordinary values compact while retaining useful precision for
    small nutrients that would disappear when rounded to two decimals."""
    value = float(value)
    if value == 0:
        return "0"
    decimals = 2 if abs(value) >= 1 else 4
    formatted = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return formatted if formatted not in {"-0", ""} else "0"


RECIPE_NUTRIENT_LABELS = {
    "fi": {
        "alcohol": "Alkoholi", "calcium": "Kalsium", "cholesterol": "Kolesteroli",
        "fatty_acids_total": "Rasvahapot yhteensä", "iron": "Rauta", "magnesium": "Magnesium",
        "potassium": "Kalium", "sodium": "Natrium", "phosphorus": "Fosfori",
        "zinc": "Sinkki", "folate": "Folaatti", "niacin": "Niasiini",
        "carotenoids": "Karotenoidit", "vitamin_b6": "B6-vitamiini",
        "riboflavin": "Riboflaviini", "thiamine": "Tiamiini",
        "vitamin_b12": "B12-vitamiini", "vitamin_c": "C-vitamiini",
        "vitamin_d": "D-vitamiini", "vitamin_e": "E-vitamiini", "vitamin_k": "K-vitamiini",
        "ala": "ALA", "dha": "DHA", "epa": "EPA", "fructose": "Fruktoosi",
        "glucose": "Glukoosi", "lactose": "Laktoosi", "maltose": "Maltoosi",
        "linoleic_acid": "Linolihappo", "mufa_cis": "MUFA cis", "pufa": "PUFA",
        "galactose": "Galaktoosi", "sucrose": "Sakkaroosi", "starch": "Tärkkelys",
        "insoluble_fiber": "Liukenematon kuitu",
        "soluble_noncellulose_polysaccharide": "Liukeneva kuitu",
        "sugar_alcohols": "Sokerialkoholit", "organic_acids": "Orgaaniset hapot",
        "trans_fat": "Transrasva", "omega3": "Omega-3", "omega6": "Omega-6",
        "sterols": "Sterolit", "iodine": "Jodi", "selenium": "Seleeni",
        "tryptophan": "Tryptofaani", "niacin_equivalent": "Niasiiniekvivalentti",
    },
    "en": {
        "alcohol": "Alcohol", "calcium": "Calcium", "cholesterol": "Cholesterol",
        "fatty_acids_total": "Total fatty acids", "iron": "Iron", "magnesium": "Magnesium",
        "potassium": "Potassium", "sodium": "Sodium", "phosphorus": "Phosphorus",
        "zinc": "Zinc", "folate": "Folate", "niacin": "Niacin",
        "carotenoids": "Carotenoids", "vitamin_b6": "Vitamin B6",
        "riboflavin": "Riboflavin", "thiamine": "Thiamine",
        "vitamin_b12": "Vitamin B12", "vitamin_c": "Vitamin C",
        "vitamin_d": "Vitamin D", "vitamin_e": "Vitamin E", "vitamin_k": "Vitamin K",
        "ala": "ALA", "dha": "DHA", "epa": "EPA", "fructose": "Fructose",
        "glucose": "Glucose", "lactose": "Lactose", "maltose": "Maltose",
        "linoleic_acid": "Linoleic acid", "mufa_cis": "MUFA cis", "pufa": "PUFA",
        "galactose": "Galactose", "sucrose": "Sucrose", "starch": "Starch",
        "insoluble_fiber": "Insoluble fiber",
        "soluble_noncellulose_polysaccharide": "Soluble non-cellulose polysaccharides",
        "sugar_alcohols": "Sugar alcohols", "organic_acids": "Organic acids",
        "trans_fat": "Trans fat", "omega3": "Omega-3", "omega6": "Omega-6",
        "sterols": "Sterols", "iodine": "Iodine", "selenium": "Selenium",
        "tryptophan": "Tryptophan", "niacin_equivalent": "Niacin equivalent",
    },
}


def _recipe_language(parent):
    """Use Finnish when the current Fineli catalog is Finnish, and English
    when a future English-only catalog supplies the ingredient names."""
    fineli_foods = [
        ingredient.fineli_food
        for ingredient in parent.ingredients.select_related("fineli_food")
        if ingredient.fineli_food is not None
    ]
    if fineli_foods and any(food.name_fi for food in fineli_foods):
        return "fi"
    if fineli_foods and any(food.name_en for food in fineli_foods):
        return "en"
    return "fi"


def _back_to_recipe(request, parent, anchor):
    """Redirect back to the recipe page at `anchor`, keeping the searches
    that were open, so adding several ingredients on a phone doesn't start
    from the top of the page every time."""
    params = {}
    for key in ("q", "own"):
        value = request.POST.get(key, "").strip()[:100]
        if value:
            params[key] = value
    url = reverse('recipe_detail', args=[parent.id])
    if params:
        url += "?" + urlencode(params)
    return redirect(f"{url}#{anchor}")


def _ingredient_rows(parent):
    """The recipe's ingredients with each one's energy, carbs and protein for
    its amount, plus totals. Per-100 g values come from the ingredient's
    source (a per-100 ml product counts its amount as ml, like the recipe
    calculation). None stays unknown; a total with an unknown part is marked
    partial."""
    rows = []
    totals = {"kcal": 0.0, "carbs": 0.0, "protein": 0.0}
    partial = {"kcal": False, "carbs": False, "protein": False}
    for ing in parent.ingredients.select_related('fineli_food', 'source_custom_food').order_by('id'):
        if ing.fineli_food is not None:
            food = ing.fineli_food
            name, kind = food.name_fi, "Fineli"
            per_100 = {"kcal": food.energy_kcal, "carbs": food.carbs_g, "protein": food.protein_g}
        else:
            food = ing.source_custom_food
            name = food.name
            kind = "Recipe" if food.kind == CustomFood.KIND_RECIPE else "Product"
            per_100 = {"kcal": food.energy_kcal_per_100g, "carbs": food.carbs_per_100g, "protein": food.protein_per_100g}
        values = {}
        for key, value in per_100.items():
            if value is None:
                values[key] = None
                partial[key] = True
            else:
                values[key] = value * ing.amount_grams / 100
                totals[key] += values[key]
        rows.append({"ingredient": ing, "name": name, "kind": kind, **values})
    return rows, totals, partial


def _recipe_detail_context(request, parent, final_values_form=None, settings_form=None):
    query = request.GET.get('q', '').strip()
    language = _recipe_language(parent)
    nutrient_labels = RECIPE_NUTRIENT_LABELS[language]
    aux_nutrients = []
    for nutrient in parent.nutrients.all().order_by('nutrient_key'):
        nutrient.display_name = nutrient_labels.get(
            nutrient.nutrient_key,
            nutrient.nutrient_key.replace("_", " ").capitalize(),
        )
        nutrient.display_value = _format_nutrient_value(nutrient.value)
        aux_nutrients.append(nutrient)
    ingredient_rows, ingredient_totals, ingredient_partial = _ingredient_rows(parent)
    nutrition_rows = [
        {"label": label, "value": getattr(parent, field), "unit": unit, "sub": sub}
        for label, field, unit, sub in (
            ("Energy", "energy_kcal_per_100g", "kcal", False),
            ("Protein", "protein_per_100g", "g", False),
            ("Carbohydrate", "carbs_per_100g", "g", False),
            ("of which sugars", "sugar_per_100g", "g", True),
            ("Fat", "fat_per_100g", "g", False),
            ("of which saturated", "sat_fat_per_100g", "g", True),
            ("Fiber", "fiber_per_100g", "g", False),
            ("Salt", "salt_per_100g", "g", False),
        )
    ]
    for row in nutrition_rows:
        # Same rounding as the page's per-100 g / serving / whole switch:
        # whole kcal, otherwise _format_nutrient_value.
        if row["value"] is None:
            row["display"] = None
        elif row["unit"] == "kcal":
            row["display"] = f"{row['value']:.0f}"
        else:
            row["display"] = _format_nutrient_value(row["value"])
    return {
        'nutrition_rows': nutrition_rows,
        'parent': parent,
        'ingredients': [row["ingredient"] for row in ingredient_rows],
        'ingredient_rows': ingredient_rows,
        'ingredient_totals': ingredient_totals,
        'ingredient_partial': ingredient_partial,
        'own_query': request.GET.get('own', '').strip(),
        # Most-used first: the My Foods picker shows the top ones until you search.
        'own_foods': CustomFood.objects.filter(user=request.user).exclude(pk=parent.pk).order_by('-use_count', 'name'),
        'favorite_fineli_foods': [
            fav.fineli_food for fav in FavoriteFood.objects.filter(
                user=request.user, fineli_food__isnull=False,
            ).select_related('fineli_food').prefetch_related('fineli_food__units').order_by('-created_at')
        ],
        'query': query,
        'fineli_results': ranked_fineli_search(query, fields=('name_fi',)) if query else [],
        'total_grams': total_recipe_grams(parent),
        'yield_grams': recipe_yield_grams(parent),
        'serving_grams': serving_grams(parent),
        'settings_form': settings_form or RecipeSettingsForm(instance=parent),
        'aux_nutrients': aux_nutrients,
        'recipe_language': language,
        'final_values_form': final_values_form or FinalNutrientValuesForm(instance=parent),
    }


@login_required
def recipe_detail_view(request, food_id):
    """Recipe composition page for one of the current user's own CustomFoods.
    A foreign-user or legacy (user=None) CustomFood 404s - never exposed."""
    parent = get_object_or_404(CustomFood, pk=food_id, user=request.user)
    return render(request, "nutrition/recipe_detail.html", _recipe_detail_context(request, parent))


def _recalculate_after_ingredient_change(request, parent, done):
    """Ingredients changed - recalculate the recipe right away (it used to
    need a separate "Recalculate" click) and tell the user what happened."""
    try:
        recalculate_custom_food_recipe_with_dependencies(parent)
    except RecipeGraphError as e:
        messages.warning(request, f"{done}, but the nutrition values could not be recalculated: {e}")
        return
    messages.success(request, f"{done} - nutrition values recalculated.")
    _update_dependent_recipes(request, parent)


def _update_dependent_recipes(request, food):
    """`food`'s values changed - recalculate the recipes that use it (also
    through other recipes) so none keeps stale values, and say which."""
    try:
        updated = recalculate_dependent_recipes(food)
    except RecipeGraphError as e:
        messages.warning(request, f"Recipes using {food.name} could not be recalculated: {e}")
        return
    if updated:
        names = ", ".join(recipe.name for recipe in updated)
        messages.info(request, f"Also recalculated the recipes that use {food.name}: {names}.")


@login_required
def add_recipe_fineli_ingredient(request, food_id):
    """Add a FineliFood portion as a recipe ingredient.

    The selected Fineli unit is converted to canonical grams from the
    persisted FineliFoodUnit row. Nutrients are still resolved later from the
    persisted food catalog, never from POST.
    """
    parent = get_object_or_404(CustomFood, pk=food_id, user=request.user)
    if request.method != "POST":
        return redirect('recipe_detail', food_id=parent.id)

    fineli_id = request.POST.get('fineli_id')
    fineli_food = FineliFood.objects.prefetch_related('units').filter(
        fineli_id=fineli_id
    ).first() if fineli_id else None
    error = None
    if fineli_food is None:
        error = "Select a valid Fineli food."
    else:
        unit_code = request.POST.get('unit', 'G')
        amount = _parse_grams(request.POST.get('amount', request.POST.get('amount_grams')))
        if amount is None or amount <= 0:
            error = "Enter a valid portion amount."
        elif unit_code == 'G':
            amount_grams = amount
        else:
            unit = fineli_food.units.filter(unit_code=unit_code).first()
            if unit is None:
                error = "Select a valid portion unit for this Fineli food."
            else:
                amount_grams = amount * unit.mass_g
    if error is None:
        ingredient = CustomFoodIngredient(
            custom_food=parent, fineli_food=fineli_food,
            amount_grams=amount_grams,
        )
        try:
            ingredient.save()  # CustomFoodIngredient.save() calls self.clean() itself
        except ValidationError as e:
            error = _validation_error_message(e)

    if error:
        context = _recipe_detail_context(request, parent)
        context['error'] = error
        return render(request, "nutrition/recipe_detail.html", context)
    _recalculate_after_ingredient_change(
        request, parent, f"Added {fineli_food.name_fi} ({amount_grams:g} g)",
    )
    return _back_to_recipe(request, parent, "add-fineli")


@login_required
def add_recipe_custom_ingredient(request, food_id):
    """Add one of the current user's own CustomFoods as a recipe ingredient.
    The source lookup is itself scoped to user=request.user, so a foreign or
    legacy CustomFood id can never even be fetched here - CustomFoodIngredient
    validation (self-reference/cycle/ownership) remains the authoritative,
    non-duplicated check for everything else."""
    parent = get_object_or_404(CustomFood, pk=food_id, user=request.user)
    if request.method != "POST":
        return redirect('recipe_detail', food_id=parent.id)

    source_id = request.POST.get('source_custom_food_id')
    source = CustomFood.objects.filter(pk=source_id, user=request.user).first() if source_id else None
    error = None
    if source is None:
        error = "Select one of your own custom foods."
    else:
        ingredient = CustomFoodIngredient(
            custom_food=parent, source_custom_food=source,
            amount_grams=_parse_grams(request.POST.get('amount_grams')),
        )
        try:
            ingredient.save()
        except ValidationError as e:
            error = _validation_error_message(e)

    if error:
        context = _recipe_detail_context(request, parent)
        context['error'] = error
        return render(request, "nutrition/recipe_detail.html", context)
    _recalculate_after_ingredient_change(
        request, parent, f"Added {source.name} ({ingredient.amount_grams:g} g)",
    )
    return _back_to_recipe(request, parent, "add-own")


@login_required
def remove_recipe_ingredient(request, food_id, ingredient_id):
    """Remove one ingredient row. Scoped by custom_food__user=request.user,
    so a foreign user's ingredient is never affected - deleting the wrong id
    (or someone else's) is a safe no-op, not an error."""
    parent = get_object_or_404(CustomFood, pk=food_id, user=request.user)
    if request.method == "POST":
        ingredient = CustomFoodIngredient.objects.filter(
            pk=ingredient_id, custom_food=parent, custom_food__user=request.user
        ).select_related('fineli_food', 'source_custom_food').first()
        if ingredient is not None:
            name = ingredient.fineli_food.name_fi if ingredient.fineli_food else ingredient.source_custom_food.name
            ingredient.delete()
            _recalculate_after_ingredient_change(request, parent, f"Removed {name}")
    return _back_to_recipe(request, parent, "ingredients")


@login_required
def recalculate_recipe_view(request, food_id):
    """Explicit, POST-only trigger for dependency-aware recipe recalculation.
    Calls only recalculate_custom_food_recipe_with_dependencies() - no lower-
    level nutrient logic lives here."""
    parent = get_object_or_404(CustomFood, pk=food_id, user=request.user)
    if request.method != "POST":
        return redirect('recipe_detail', food_id=parent.id)

    error = None
    try:
        recalculate_custom_food_recipe_with_dependencies(parent)
    except RecipeGraphError as e:
        error = str(e)

    if error:
        context = _recipe_detail_context(request, parent)
        context['error'] = error
        return render(request, "nutrition/recipe_detail.html", context)
    messages.success(request, "Nutrition values recalculated from the ingredients.")
    _update_dependent_recipes(request, parent)
    return _back_to_recipe(request, parent, "final-values")


@login_required
def update_recipe_settings(request, food_id):
    """Rename a recipe and set its optional servings / finished weight. The
    finished weight changes the per-100 g basis, so recalculate."""
    parent = get_object_or_404(CustomFood, pk=food_id, user=request.user)
    if request.method != "POST":
        return redirect('recipe_detail', food_id=parent.id)
    form = RecipeSettingsForm(request.POST, instance=parent)
    if not form.is_valid():
        return render(request, "nutrition/recipe_detail.html",
                      _recipe_detail_context(request, parent, settings_form=form))
    weight_changed = 'cooked_weight_g' in form.changed_data
    form.save()
    if weight_changed and parent.ingredients.exists():
        _recalculate_after_ingredient_change(request, parent, "Recipe details saved")
    else:
        messages.success(request, "Recipe details saved.")
    return _back_to_recipe(request, parent, "recipe-details")


@login_required
def duplicate_recipe(request, food_id):
    """Copy a recipe - ingredients, yield and nutrition values - as a
    starting point for a variation."""
    source = get_object_or_404(CustomFood, pk=food_id, user=request.user, kind=CustomFood.KIND_RECIPE)
    if request.method != "POST":
        return redirect('recipe_detail', food_id=source.id)
    with transaction.atomic():
        ingredients = list(source.ingredients.all())
        nutrients = list(source.nutrients.all())
        copy = CustomFood.objects.get(pk=source.pk)
        copy.pk = None
        copy._state.adding = True
        copy.name = f"{source.name} (copy)"[:200]
        copy.use_count = 0
        copy.save()
        for ing in ingredients:
            CustomFoodIngredient.objects.create(
                custom_food=copy, fineli_food=ing.fineli_food,
                source_custom_food=ing.source_custom_food, amount_grams=ing.amount_grams,
            )
        CustomFoodNutrient.objects.bulk_create([
            CustomFoodNutrient(custom_food=copy, nutrient_key=n.nutrient_key, value=n.value,
                               unit=n.unit, qualifier=n.qualifier)
            for n in nutrients
        ])
    messages.success(request, f"Copied as \"{copy.name}\" - rename it under Recipe details.")
    return redirect('recipe_detail', food_id=copy.id)


@login_required
def delete_recipe(request, food_id):
    """Delete a recipe. Diary entries already logged from it keep their own
    stored values (the link to the recipe is cleared)."""
    recipe = get_object_or_404(CustomFood, pk=food_id, user=request.user, kind=CustomFood.KIND_RECIPE)
    if request.method == "POST":
        used_in = _recipes_using(recipe)
        if used_in:
            messages.error(request, f"{recipe.name} is an ingredient in {used_in} - remove it there first.")
            return redirect('recipe_detail', food_id=recipe.id)
        name = recipe.name
        recipe.delete()
        messages.success(request, f"Deleted {name}.")
    return redirect('recipe_list')


def _recipes_using(food):
    """Names of recipes that use `food` as an ingredient ("" if none).
    Deleting it would silently drop those ingredient rows (CASCADE) and
    leave the recipes' values stale, so deletion is refused instead."""
    names = sorted(set(
        CustomFoodIngredient.objects.filter(source_custom_food=food).values_list('custom_food__name', flat=True)
    ))
    return ", ".join(names)


@login_required
def save_recipe_final_values(request, food_id):
    """Manually correct the parent recipe's own final per-100g core
    nutrient fields. Edits ONLY those typed fields - never touches
    ingredients and never triggers recalculation. The values stay until the
    ingredients change (which recalculates) or 'Recalculate' is pressed."""
    parent = get_object_or_404(CustomFood, pk=food_id, user=request.user)
    if request.method != "POST":
        return redirect('recipe_detail', food_id=parent.id)

    form = FinalNutrientValuesForm(request.POST, instance=parent)
    if form.is_valid():
        form.save()
        messages.success(request, "Final nutrition values saved.")
        _update_dependent_recipes(request, parent)
        return _back_to_recipe(request, parent, "final-values")

    context = _recipe_detail_context(request, parent, final_values_form=form)
    return render(request, "nutrition/recipe_detail.html", context)


def _optional_float_from_post(post_data, key):
    """None for a missing/blank field (unknown), float otherwise - including
    explicit "0". Does not fabricate zero for an omitted nutrient."""
    raw = post_data.get(key)
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    return float(raw)


@login_required
def add_meal_entry(request):
    """Add a meal entry to a specific date."""
    if request.method == "POST":
        date_str = request.POST.get('date')

        from datetime import datetime
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else localdate()
        daily_log, _ = DailyLog.objects.get_or_create(date=date_obj)

        meal_type = request.POST.get('meal_type', 'snack')

        entry = MealEntry.objects.create(
            daily_log=daily_log,
            user=request.user,
            source_type='manual',
            food_name=request.POST.get('food_name'),
            meal_type=meal_type,
            portion_grams=float(request.POST.get('portion_grams', 100)),
            calories=_optional_float_from_post(request.POST, 'calories'),
            protein_g=_optional_float_from_post(request.POST, 'protein_g'),
            carbs_g=_optional_float_from_post(request.POST, 'carbs_g'),
            sugar_g=_optional_float_from_post(request.POST, 'sugar_g'),
            fat_g=_optional_float_from_post(request.POST, 'fat_g'),
            sat_fat_g=_optional_float_from_post(request.POST, 'sat_fat_g'),
            fiber_g=_optional_float_from_post(request.POST, 'fiber_g'),
            salt_g=_optional_float_from_post(request.POST, 'salt_g'),
        )
        
        daily_log.recalculate_from_meals()
        
        return redirect(f'/food-diary/?date={date_obj.strftime("%Y-%m-%d")}')
    
    date_str = request.GET.get('date', localdate().strftime('%Y-%m-%d'))
    form = MealEntryForm()
    
    own_custom_foods = CustomFood.objects.filter(user=request.user)[:20] if request.user.is_authenticated else CustomFood.objects.none()
    return render(request, "nutrition/add_meal_entry.html", {
        "form": form,
        "date": date_str,
        "custom_foods": own_custom_foods,
    })


@login_required
def edit_meal_entry(request, entry_id):
    """Edit an existing meal entry. Scoped to request.user so one user's
    entry ID cannot be read or mutated by another user."""
    entry = get_object_or_404(MealEntry, pk=entry_id, user=request.user)
    diary_url = f"{reverse('food_diary')}?date={entry.daily_log.date:%Y-%m-%d}"
    original_grams = entry.portion_grams
    original_amount = entry.source_amount
    unit_label = entry.portion_label.split(" × ", 1)[1] if entry.portion_label else ""

    if request.method == "POST":
        form = MealEntryEditForm(request.POST, instance=entry)
        if form.is_valid():
            new_grams = form.cleaned_data["portion_grams"]
            new_amount = form.cleaned_data.get("portion_amount")
            if unit_label and new_amount and new_amount != original_amount and original_amount:
                # "2 × iso (kpl)" -> "3 × iso (kpl)": grams follow the count.
                new_grams = new_amount * original_grams / original_amount
                entry.portion_grams = new_grams
                entry.source_amount = new_amount
            elif new_grams != original_grams:
                # Grams typed by hand - the portion no longer describes it.
                entry.source_amount, entry.source_unit = None, ''
            ratio = new_grams / original_grams if original_grams else None
            values_edited = any(name in form.changed_data for name in NUTRIENT_ENTRY_FIELDS)
            with transaction.atomic():
                if ratio and ratio != 1:
                    if not values_edited:
                        # Nutrient values follow the amount unless edited by hand.
                        for name in NUTRIENT_ENTRY_FIELDS:
                            value = getattr(entry, name)
                            setattr(entry, name, None if value is None else value * ratio)
                    aux = list(entry.nutrients.all())
                    for nutrient in aux:
                        nutrient.value *= ratio
                    MealEntryNutrient.objects.bulk_update(aux, ["value"])
                entry.save()  # also refreshes the day's totals
            messages.success(request, f"Saved {entry.food_name}.")
            return redirect(diary_url)
    else:
        form = MealEntryEditForm(instance=entry)

    return render(request, "nutrition/edit_meal_entry.html", {
        "form": form, "entry": entry, "diary_url": diary_url, "unit_label": unit_label,
        "grams_per_unit": original_grams / original_amount if unit_label and original_amount else None,
    })


@login_required
def delete_meal_entry(request, entry_id):
    """Delete a meal entry. Scoped to request.user so one user's entry ID
    cannot be read or mutated by another user."""
    entry = get_object_or_404(MealEntry, pk=entry_id, user=request.user)
    date = entry.daily_log.date

    if request.method == "POST":
        entry.delete()
    # No separate confirmation page - the diary confirms with a second click.
    return redirect(f'/food-diary/?date={date.strftime("%Y-%m-%d")}')


def _optional_float(value):
    """None (unknown) stays None instead of crashing float(None)."""
    return None if value is None else float(value)


def format_food_for_frontend(food, is_favorite=False):
    """Format FineliFood object for JSON response."""
    return {
        'id': food.fineli_id,
        'name': food.name_fi,
        'name_en': food.name_en or food.name_fi,
        'energy_kcal': _optional_float(food.energy_kcal),
        'protein': _optional_float(food.protein_g),
        'carbs': _optional_float(food.carbs_g),
        'sugar': _optional_float(food.sugar_g),
        'fat': _optional_float(food.fat_g),
        'sat_fat': _optional_float(food.sat_fat_g),
        'fiber': _optional_float(food.fiber_g),
        'salt': _optional_float(food.salt_g),
        'is_favorite': is_favorite,
        'usage_count': food.usage_count,
        # Fineli portion sizes for the portion picker, e.g.
        # {'code': 'KPL_L', 'label': 'iso (kpl)', 'grams': 150.0}.
        'units': [
            {'code': u.unit_code, 'label': u.label, 'grams': u.mass_g}
            for u in sorted(food.units.all(), key=lambda u: fineli_unit_sort_key(u.unit_code))
        ],
    }


def format_custom_food_for_frontend(food):
    """Format CustomFood object for JSON response."""
    return {
        'id': food.id,
        'name': food.name,
        'brand': food.brand or '',
        'display_name': f"{food.name}" + (f" ({food.brand})" if food.brand else ""),
        'energy_kcal': _optional_float(food.energy_kcal_per_100g),
        'protein': _optional_float(food.protein_per_100g),
        'carbs': _optional_float(food.carbs_per_100g),
        'sugar': _optional_float(food.sugar_per_100g),
        'fat': _optional_float(food.fat_per_100g),
        'sat_fat': _optional_float(food.sat_fat_per_100g),
        'fiber': _optional_float(food.fiber_per_100g),
        'salt': _optional_float(food.salt_per_100g),
        'package_amount': float(food.package_amount) if food.package_amount else None,
        'package_unit': food.package_unit,
        'nutrition_basis': food.nutrition_basis,
        'default_portion': float(food.default_portion_g) if food.default_portion_g else 100,
        # Recipes are logged in grams; products also offer "1 × pakkaus".
        'is_recipe': food.kind == CustomFood.KIND_RECIPE,
        'serving_grams': serving_grams(food) if food.kind == CustomFood.KIND_RECIPE else None,
        'use_count': food.use_count,
        'source': 'custom',
    }


def list_custom_foods_api(request):
    """API endpoint to list the current user's own custom foods for the Add
    Food modal. Anonymous requests see no custom foods (they have none)."""
    if not request.user.is_authenticated:
        return JsonResponse({'results': [], 'count': 0})

    query = request.GET.get('q', '').strip().lower()

    foods = CustomFood.objects.filter(user=request.user).order_by('-use_count', 'name')
    
    if query:
        foods = foods.filter(name_search(['name', 'brand'], query))
    
    results = [format_custom_food_for_frontend(food) for food in foods[:30]]

    return JsonResponse({
        'results': results,
        'count': len(results)
    })


def search_fineli_api(request):
    """Search Fineli database (local only)."""
    query = request.GET.get('q', '').strip()
    if len(query) < 2:
        favorites = FineliFood.objects.filter(usage_count__gt=0).prefetch_related('units').order_by('-usage_count', '-last_used')[:10]
        results = [format_food_for_frontend(food, is_favorite=True) for food in favorites]
        return JsonResponse({
            'results': results,
            'source': 'favorites',
            'count': len(results)
        })
    
    try:
        foods = ranked_fineli_search(query, by_usage=True)
        
        results = [format_food_for_frontend(food, is_favorite=(food.usage_count > 0)) for food in foods]
        
        db_count = FineliFood.objects.count()
        
        return JsonResponse({
            'results': results,
            'source': 'database',
            'count': len(results),
            'total_foods_in_db': db_count
        })
    except Exception as e:
        return JsonResponse({'error': str(e), 'results': [], 'source': 'error', 'count': 0}, status=500)


def add_fineli_entry(request):
    """Add a meal entry from Fineli or custom food."""
    # Manual check (not @login_required): called via fetch() from
    # food_diary.html, which always does response.json() - a login-page
    # redirect would break that parsing.
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    if request.method == "POST":
        data = json.loads(request.body)
        
        date_str = data.get('date')
        from datetime import datetime
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else localdate()
        daily_log, _ = DailyLog.objects.get_or_create(date=date_obj)
        
        fineli_id = data.get('fineli_id')
        custom_food_id = data.get('custom_food_id')
        source_type = data.get('source', 'fineli')
        fineli_food = None
        custom_food = None
        
        if source_type == 'custom' and custom_food_id and request.user.is_authenticated:
            # Handle custom food - ownership-scoped: a user may only quick-add
            # their own custom food, never another user's or a legacy
            # (user=None) one, even by sending its ID directly.
            custom_food = CustomFood.objects.filter(id=custom_food_id, user=request.user).first()
            if custom_food is None:
                return JsonResponse({'error': 'Unknown food'}, status=404)
        elif fineli_id:
            # Only foods in the local Fineli catalog - a client can no longer
            # create catalog rows (with its own values) by logging an id.
            fineli_food = FineliFood.objects.filter(fineli_id=fineli_id).first()
            if fineli_food is None:
                return JsonResponse({'error': 'Unknown Fineli food'}, status=404)
            source_type = 'fineli'

        # A Fineli food can be logged as amount x Fineli unit (e.g. 1 x KPL_L).
        # Grams then come from the stored unit size, never from the client.
        source_amount, source_unit = None, ''
        unit_code = data.get('unit')
        if fineli_food is not None and unit_code:
            try:
                amount = float(data.get('amount'))
            except (TypeError, ValueError):
                return JsonResponse({'error': 'Invalid amount'}, status=400)
            if amount <= 0:
                return JsonResponse({'error': 'Invalid amount'}, status=400)
            if unit_code == 'G':
                portion_grams = amount
            else:
                unit = fineli_food.units.filter(unit_code=unit_code).first()
                if unit is None:
                    return JsonResponse({'error': f'Unknown portion unit: {unit_code}'}, status=400)
                portion_grams = amount * unit.mass_g
            source_amount, source_unit = amount, unit_code
        elif custom_food is not None and unit_code:
            # Custom products use the package unit or a compatible direct unit.
            try:
                amount = float(data.get('amount'))
            except (TypeError, ValueError):
                return JsonResponse({'error': 'Invalid amount'}, status=400)
            if amount <= 0:
                return JsonResponse({'error': 'Invalid amount'}, status=400)
            if unit_code == 'SERV':
                # One serving of a recipe - only when the recipe has servings.
                grams_per_serving = (
                    serving_grams(custom_food) if custom_food.kind == CustomFood.KIND_RECIPE else None
                )
                if not grams_per_serving:
                    return JsonResponse({'error': 'This recipe has no servings set'}, status=400)
                portion_grams = amount * grams_per_serving
                source_amount, source_unit = amount, unit_code
            else:
                package_amount = custom_food.package_amount or custom_food.default_portion_g
                package_unit = custom_food.package_unit or "G"
                unit_to_ml = {"ML": 1, "DL": 100, "L": 1000}
                if unit_code == 'PKG' and package_amount:
                    normalized_amount = amount * package_amount
                    normalized_unit = package_unit
                elif unit_code in {"G", "ML", "DL", "L"}:
                    normalized_amount = amount
                    normalized_unit = unit_code
                else:
                    return JsonResponse({'error': f'Unknown portion unit: {unit_code}'}, status=400)
                if custom_food.nutrition_basis == "ML":
                    if normalized_unit == "G":
                        return JsonResponse({'error': 'This food must be logged in a volume unit'}, status=400)
                    portion_grams = normalized_amount * unit_to_ml[normalized_unit]
                else:
                    if normalized_unit != "G":
                        return JsonResponse({'error': 'This food must be logged in grams'}, status=400)
                    portion_grams = normalized_amount
                source_amount, source_unit = amount, unit_code
        else:
            portion_grams = float(data.get('portion_grams', 100))
        multiplier = portion_grams / 100.0

        # Usage counters only after the request has been validated.
        if fineli_food is not None:
            fineli_food.usage_count += 1
            fineli_food.last_used = now()
            fineli_food.save()
        if custom_food is not None:
            custom_food.use_count += 1
            custom_food.save()

        # Per-100 g (or ml) values come from the database for catalog and own
        # foods - never from the browser, whose copy may be stale (opened
        # before a recipe or product changed). Only a food without a stored
        # source uses the values sent with the request. None stays unknown.
        if fineli_food is not None:
            food_name = fineli_food.name_fi
            per_100 = {
                'calories': fineli_food.energy_kcal, 'protein_g': fineli_food.protein_g,
                'carbs_g': fineli_food.carbs_g, 'sugar_g': fineli_food.sugar_g,
                'fat_g': fineli_food.fat_g, 'sat_fat_g': fineli_food.sat_fat_g,
                'fiber_g': fineli_food.fiber_g, 'salt_g': fineli_food.salt_g,
            }
        elif custom_food is not None:
            food_name = custom_food.name + (f" ({custom_food.brand})" if custom_food.brand else "")
            per_100 = {
                'calories': custom_food.energy_kcal_per_100g, 'protein_g': custom_food.protein_per_100g,
                'carbs_g': custom_food.carbs_per_100g, 'sugar_g': custom_food.sugar_per_100g,
                'fat_g': custom_food.fat_per_100g, 'sat_fat_g': custom_food.sat_fat_per_100g,
                'fiber_g': custom_food.fiber_per_100g, 'salt_g': custom_food.salt_per_100g,
            }
        else:
            food_name = data.get('name', '')
            per_100 = {
                'calories': data.get('energy_kcal'), 'protein_g': data.get('protein'),
                'carbs_g': data.get('carbs'), 'sugar_g': data.get('sugar'),
                'fat_g': data.get('fat'), 'sat_fat_g': data.get('sat_fat'),
                'fiber_g': data.get('fiber'), 'salt_g': data.get('salt'),
            }
        scaled = {key: None if value is None else value * multiplier for key, value in per_100.items()}

        with transaction.atomic():
            entry = MealEntry.objects.create(
                daily_log=daily_log,
                user=request.user,
                fineli_food=fineli_food,
                custom_food=custom_food,
                source_type=source_type,
                food_name=food_name,
                meal_type=data.get('meal_type', 'snack'),
                portion_grams=portion_grams,
                source_amount=source_amount,
                source_unit=source_unit,
                **scaled,
            )

            # Auxiliary nutrients come from the persisted FineliFoodNutrient
            # catalog, not the client payload - missing source rows stay
            # missing (unknown), never fabricated as 0.
            if fineli_food is not None:
                MealEntryNutrient.objects.bulk_create([
                    MealEntryNutrient(
                        meal_entry=entry,
                        nutrient_key=aux.nutrient_key,
                        value=aux.value * multiplier,
                        unit=aux.unit,
                        qualifier=aux.qualifier,
                    )
                    for aux in fineli_food.nutrients.all()
                ])

            # Same pattern for custom_food: auxiliary nutrients come from the
            # persisted CustomFoodNutrient rows, not the client payload -
            # missing source rows stay missing (unknown), never fabricated.
            # custom_food is only set above when it passed the request.user
            # ownership check, so this never reads another user's rows.
            if custom_food is not None:
                MealEntryNutrient.objects.bulk_create([
                    MealEntryNutrient(
                        meal_entry=entry,
                        nutrient_key=aux.nutrient_key,
                        value=aux.value * multiplier,
                        unit=aux.unit,
                        qualifier=aux.qualifier,
                    )
                    for aux in custom_food.nutrients.all()
                ])

        daily_log.recalculate_from_meals()
        
        return JsonResponse({'success': True})
    
    return JsonResponse({'error': 'Invalid request'}, status=400)


@login_required
def manage_favorites(request):
    """Favorites page: the user's starred Fineli foods and own foods, with
    search to star more. Starring/unstarring uses the favorites JSON API."""
    favorites = FavoriteFood.objects.filter(user=request.user).select_related(
        'fineli_food', 'custom_food',
    ).order_by('-created_at')
    return render(request, "nutrition/favorites.html", {'favorites': favorites})


def add_favorite_food(request):
    """Favorite a FineliFood or CustomFood for the current user. Idempotent:
    favoriting an already-favorited food is a no-op, not a duplicate row."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    data = json.loads(request.body)
    fineli_id = data.get('fineli_id')
    custom_food_id = data.get('custom_food_id')

    if fineli_id:
        food = get_object_or_404(FineliFood, fineli_id=fineli_id)
        FavoriteFood.objects.get_or_create(user=request.user, fineli_food=food)
    elif custom_food_id:
        # Ownership-scoped: a user may only favorite their own custom food,
        # never another user's or a legacy (user=None) one.
        food = get_object_or_404(CustomFood, id=custom_food_id, user=request.user)
        FavoriteFood.objects.get_or_create(user=request.user, custom_food=food)
    else:
        return JsonResponse({'error': 'fineli_id or custom_food_id required'}, status=400)

    return JsonResponse({'success': True, 'favorited': True})


def remove_favorite_food(request):
    """Unfavorite a FineliFood or CustomFood for the current user. Idempotent:
    removing a non-favorite is a safe no-op, not an error."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    data = json.loads(request.body)
    fineli_id = data.get('fineli_id')
    custom_food_id = data.get('custom_food_id')

    if fineli_id:
        FavoriteFood.objects.filter(user=request.user, fineli_food__fineli_id=fineli_id).delete()
    elif custom_food_id:
        FavoriteFood.objects.filter(user=request.user, custom_food_id=custom_food_id).delete()
    else:
        return JsonResponse({'error': 'fineli_id or custom_food_id required'}, status=400)

    return JsonResponse({'success': True, 'favorited': False})


def list_favorite_foods(request):
    """List the current user's favorited foods."""
    if not request.user.is_authenticated:
        return JsonResponse({'results': [], 'count': 0})

    favorites = FavoriteFood.objects.filter(user=request.user) \
        .select_related('fineli_food', 'custom_food').prefetch_related('fineli_food__units').order_by('-created_at')
    results = []
    for fav in favorites:
        if fav.fineli_food_id:
            results.append(format_food_for_frontend(fav.fineli_food, is_favorite=True))
        else:
            entry = format_custom_food_for_frontend(fav.custom_food)
            entry['is_favorite'] = True
            results.append(entry)
    return JsonResponse({'results': results, 'count': len(results)})


def _recent_food_entries(user, limit=10):
    """Distinct logical foods from `user`'s own MealEntry history, most-
    recently-used first (one row per FineliFood/CustomFood identity).
    Legacy rows with user=None are never included here."""
    seen = set()
    recent = []
    entries = MealEntry.objects.filter(
        Q(fineli_food__isnull=False) | Q(custom_food__isnull=False), user=user
    ).select_related('fineli_food', 'custom_food').order_by('-created_at')
    for entry in entries:
        key = ('fineli', entry.fineli_food_id) if entry.fineli_food_id else ('custom', entry.custom_food_id)
        if key in seen:
            continue
        seen.add(key)
        recent.append(entry)
        if len(recent) >= limit:
            break
    return recent


def list_recent_foods(request):
    """Recently used foods, derived fresh from the current user's own
    MealEntry history (no maintained counter). Anonymous requests get no
    history to scope to, so they see an empty list."""
    if not request.user.is_authenticated:
        return JsonResponse({'results': [], 'count': 0})

    results = []
    for entry in _recent_food_entries(request.user):
        if entry.fineli_food_id:
            results.append(format_food_for_frontend(entry.fineli_food))
        else:
            results.append(format_custom_food_for_frontend(entry.custom_food))
    return JsonResponse({'results': results, 'count': len(results)})


def list_frequent_foods(request):
    """Most-used foods by MealEntry occurrence count within the current
    user's own history, counted fresh each request (not
    FineliFood.usage_count/CustomFood.use_count - those are separate,
    pre-existing counters left untouched). Anonymous requests see an empty
    list; legacy user=None rows are never counted."""
    from django.db.models import Count

    if not request.user.is_authenticated:
        return JsonResponse({'results': [], 'count': 0})

    fineli_counts = list(
        MealEntry.objects.filter(fineli_food__isnull=False, user=request.user)
        .values('fineli_food_id').annotate(count=Count('id')).order_by('-count', 'fineli_food_id')
    )
    custom_counts = list(
        MealEntry.objects.filter(custom_food__isnull=False, user=request.user)
        .values('custom_food_id').annotate(count=Count('id')).order_by('-count', 'custom_food_id')
    )

    combined = (
        [('fineli', row['fineli_food_id'], row['count']) for row in fineli_counts]
        + [('custom', row['custom_food_id'], row['count']) for row in custom_counts]
    )
    combined.sort(key=lambda row: (-row[2], row[0], row[1]))
    combined = combined[:10]

    fineli_foods = {f.id: f for f in FineliFood.objects.filter(
        id__in=[food_id for source, food_id, _ in combined if source == 'fineli']
    ).prefetch_related('units')}
    custom_foods = {f.id: f for f in CustomFood.objects.filter(
        id__in=[food_id for source, food_id, _ in combined if source == 'custom']
    )}

    results = []
    for source, food_id, count in combined:
        food = fineli_foods.get(food_id) if source == 'fineli' else custom_foods.get(food_id)
        if food is None:
            continue
        entry = format_food_for_frontend(food) if source == 'fineli' else format_custom_food_for_frontend(food)
        entry['use_count_in_history'] = count
        results.append(entry)
    return JsonResponse({'results': results, 'count': len(results)})


def bulk_import_diary_csv(request):
    """Bulk import food-level diary rows from uploaded Fineli diary CSV files as MealEntry records."""
    # Manual check (not @login_required): called via fetch() from
    # food_diary.html, which always does response.json() - a login-page
    # redirect would break that parsing.
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    if request.method != "POST":
        return JsonResponse({'error': 'POST required'}, status=405)

    files = request.FILES.getlist('csv_files')

    if not files:
        return JsonResponse({'error': 'No files uploaded'}, status=400)

    from datetime import datetime
    from django.db import transaction
    from .utils import parse_food_rows_from_content

    file_results = []
    rows_by_date = {}  # date_str -> list of food-row dicts, merged across all uploaded files

    for uploaded_file in files:
        try:
            content = uploaded_file.read().decode('utf-8-sig')
            file_days = parse_food_rows_from_content(content)

            if not file_days:
                file_results.append({
                    'filename': uploaded_file.name,
                    'status': 'error',
                    'message': 'No food rows found in file'
                })
                continue

            file_row_count = 0
            for date_str, rows in file_days.items():
                rows_by_date.setdefault(date_str, []).extend(rows)
                file_row_count += len(rows)

            file_results.append({
                'filename': uploaded_file.name,
                'status': 'success',
                'imported': file_row_count,
                'updated': 0
            })

        except ValueError as e:
            file_results.append({
                'filename': uploaded_file.name,
                'status': 'error',
                'message': f'CSV format error: {str(e)}'
            })
        except Exception as e:
            file_results.append({
                'filename': uploaded_file.name,
                'status': 'error',
                'message': str(e)
            })

    failed_files = [result for result in file_results if result['status'] == 'error']
    if failed_files:
        return JsonResponse({
            'success': False,
            'error': 'No files were imported because one or more files could not be validated.',
            'files': file_results,
        }, status=400)

    total_new_days = 0
    total_updated_days = 0
    total_food_rows = 0

    owner = request.user

    def row_key(date_str, row):
        """Identify one diary food row without using nutrition values."""
        return (
            date_str,
            row['meal_type'],
            row['source_food_id'],
            row['food_name'],
            row['source_amount'],
            row['source_unit'],
            row['portion_grams'],
        )

    # Uploads can contain overlapping exports or the same file more than once.
    # Keep one copy of each food row before touching the database.
    for date_str, rows in list(rows_by_date.items()):
        unique_rows = {}
        for row in rows:
            unique_rows[row_key(date_str, row)] = row
        rows_by_date[date_str] = list(unique_rows.values())

    def meal_entry_values(row):
        return {
            'food_name': row['food_name'],
            'meal_type': row['meal_type'],
            'portion_grams': row['portion_grams'],
            'calories': row['calories'],
            'protein_g': row['protein_g'],
            'protein_qualifier': row['protein_qualifier'],
            'carbs_g': row['carbs_g'],
            'carbs_qualifier': row['carbs_qualifier'],
            'fat_g': row['fat_g'],
            'fat_qualifier': row['fat_qualifier'],
            'sat_fat_g': row['sat_fat_g'],
            'sat_fat_qualifier': row['sat_fat_qualifier'],
            'sugar_g': row['sugar_g'],
            'sugar_qualifier': row['sugar_qualifier'],
            'fiber_g': row['fiber_g'],
            'fiber_qualifier': row['fiber_qualifier'],
            'salt_g': row['salt_g'],
            'salt_qualifier': row['salt_qualifier'],
            'source_food_id': row['source_food_id'],
            'source_amount': row['source_amount'],
            'source_unit': row['source_unit'],
            'source_ingredients_classification': row['source_ingredients_classification'],
            'source_food_use_classification': row['source_food_use_classification'],
        }

    class DuplicateImportRows(Exception):
        """Abort the complete upload when the existing data is ambiguous."""

    try:
        # A previous implementation deleted and recreated all rows for an
        # imported date.  Never do that: an upload must be additive and
        # idempotent, even when it overlaps an earlier export.
        with transaction.atomic():
            for date_str in sorted(rows_by_date):
                rows = rows_by_date[date_str]
                day_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                # Serialise imports for the same day.  Without the row lock,
                # two simultaneous uploads could both observe a missing row
                # and create duplicates before either transaction commits.
                daily_log, created = (
                    DailyLog.objects.select_for_update().get_or_create(date=day_date)
                )
                existing_entries = list(
                    daily_log.meal_entries.select_for_update().filter(
                        source_type='bulk_import', user=owner
                    )
                )
                existing = {
                    row_key(date_str, {
                        'meal_type': entry.meal_type,
                        'source_food_id': entry.source_food_id,
                        'food_name': entry.food_name,
                        'source_amount': entry.source_amount,
                        'source_unit': entry.source_unit,
                        'portion_grams': entry.portion_grams,
                    }): entry
                    for entry in existing_entries
                }

                if len(existing) != len(existing_entries):
                    raise DuplicateImportRows(date_str)

                for row in rows:
                    key = row_key(date_str, row)
                    entry = existing.get(key)
                    if entry is None:
                        entry = MealEntry(
                            daily_log=daily_log,
                            user=owner,
                            source_type='bulk_import',
                            **meal_entry_values(row),
                        )
                        entry.save()
                        existing[key] = entry
                    else:
                        values = meal_entry_values(row)
                        for field, value in values.items():
                            setattr(entry, field, value)
                        entry.save(update_fields=list(values) + ['created_at'])

                    entry.nutrients.all().delete()
                    MealEntryNutrient.objects.bulk_create([
                        MealEntryNutrient(
                            meal_entry=entry,
                            nutrient_key=aux['nutrient_key'],
                            value=aux['value'],
                            unit=aux['unit'],
                            qualifier=aux['qualifier'],
                        )
                        for aux in row['aux_nutrients']
                    ])
                    total_food_rows += 1

                daily_log.recalculate_from_meals()
                if created:
                    total_new_days += 1
                else:
                    total_updated_days += 1
    except DuplicateImportRows as error:
        return JsonResponse({
            'success': False,
            'error': (
                f'Import stopped: duplicate existing CSV rows were found '
                f'for {error.args[0]}. No changes were made.'
            ),
            'files': file_results,
        }, status=409)

    return JsonResponse({
        'success': True,
        'total_imported': total_new_days,
        'total_updated': total_updated_days,
        'files': file_results,
        'message': f'Imported {total_food_rows} food entries across {total_new_days} new day(s) and {total_updated_days} updated day(s) from {len(files)} file(s)'
    })
