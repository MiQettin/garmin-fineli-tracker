from datetime import timedelta
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect, get_object_or_404
from django.utils.timezone import now
from django.http import JsonResponse
from django.db import transaction
from django.db.models import Q
import json

from core.models import DailyLog
from .models import FineliFood, CustomFood, CustomFoodIngredient, FavoriteFood, MealEntry, MealEntryNutrient
from .forms import CustomFoodForm, FinalNutrientValuesForm, MealEntryForm, RecipeCreateForm
from .recipe_aggregation import total_recipe_grams
from .recipe_persistence import RecipeGraphError, recalculate_custom_food_recipe_with_dependencies


@login_required
def food_diary_view(request):
    """View for managing daily food diary."""
    date_str = request.GET.get('date')
    if date_str:
        from datetime import datetime
        current_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    else:
        current_date = now().date()
    
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

    for entry in meal_entries:
        totals = meal_totals[entry.meal_type]
        totals['calories'] += entry.calories
        totals['protein'] += entry.protein_g
        totals['carbs'] += entry.carbs_g
        totals['sugar'] += entry.sugar_g
        totals['fat'] += entry.fat_g
        totals['sat_fat'] += entry.sat_fat_g
        totals['fiber'] += entry.fiber_g
        totals['salt'] += entry.salt_g

    prev_date = current_date - timedelta(days=1)
    next_date = current_date + timedelta(days=1)

    context = {
        'current_date': current_date,
        'daily_log': daily_log,
        'meal_entries': meal_entries,
        'custom_foods': custom_foods,
        'meal_totals': meal_totals,
        'meal_types': meal_types,
        'prev_date': prev_date,
        'next_date': next_date,
        'is_today': current_date == now().date(),
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

    return render(request, "nutrition/add_custom_food.html", {"form": form})


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
            recipe.save()
            return redirect('recipe_detail', food_id=recipe.id)
    else:
        form = RecipeCreateForm()

    return render(request, "nutrition/create_recipe.html", {"form": form})


@login_required
def manage_custom_foods(request):
    """Manage the current user's own custom foods only."""
    custom_foods = CustomFood.objects.filter(user=request.user)

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
            form.save()
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
        food.delete()
        return redirect('manage_custom_foods')

    return render(request, "nutrition/delete_custom_food.html", {"food": food})


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


def _recipe_detail_context(request, parent, final_values_form=None):
    query = request.GET.get('q', '').strip()
    return {
        'parent': parent,
        'ingredients': parent.ingredients.select_related('fineli_food', 'source_custom_food').order_by('id'),
        'own_foods': CustomFood.objects.filter(user=request.user).exclude(pk=parent.pk).order_by('name'),
        'query': query,
        'fineli_results': FineliFood.objects.filter(name_fi__icontains=query).order_by('name_fi')[:20] if query else [],
        'total_grams': total_recipe_grams(parent),
        'aux_nutrients': parent.nutrients.all().order_by('nutrient_key'),
        'final_values_form': final_values_form or FinalNutrientValuesForm(instance=parent),
    }


@login_required
def recipe_detail_view(request, food_id):
    """Recipe composition page for one of the current user's own CustomFoods.
    A foreign-user or legacy (user=None) CustomFood 404s - never exposed."""
    parent = get_object_or_404(CustomFood, pk=food_id, user=request.user)
    return render(request, "nutrition/recipe_detail.html", _recipe_detail_context(request, parent))


@login_required
def add_recipe_fineli_ingredient(request, food_id):
    """Add a shared FineliFood as a recipe ingredient. Only identity+grams
    are read from POST - nutrient values always come from the persisted
    FineliFood/FineliFoodNutrient rows via recipe aggregation, never here."""
    parent = get_object_or_404(CustomFood, pk=food_id, user=request.user)
    if request.method != "POST":
        return redirect('recipe_detail', food_id=parent.id)

    fineli_id = request.POST.get('fineli_id')
    fineli_food = FineliFood.objects.filter(fineli_id=fineli_id).first() if fineli_id else None
    error = None
    if fineli_food is None:
        error = "Select a valid Fineli food."
    else:
        ingredient = CustomFoodIngredient(
            custom_food=parent, fineli_food=fineli_food,
            amount_grams=_parse_grams(request.POST.get('amount_grams')),
        )
        try:
            ingredient.save()  # CustomFoodIngredient.save() calls self.clean() itself
        except ValidationError as e:
            error = _validation_error_message(e)

    if error:
        context = _recipe_detail_context(request, parent)
        context['error'] = error
        return render(request, "nutrition/recipe_detail.html", context)
    return redirect('recipe_detail', food_id=parent.id)


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
    return redirect('recipe_detail', food_id=parent.id)


@login_required
def remove_recipe_ingredient(request, food_id, ingredient_id):
    """Remove one ingredient row. Scoped by custom_food__user=request.user,
    so a foreign user's ingredient is never affected - deleting the wrong id
    (or someone else's) is a safe no-op, not an error."""
    parent = get_object_or_404(CustomFood, pk=food_id, user=request.user)
    if request.method == "POST":
        CustomFoodIngredient.objects.filter(
            pk=ingredient_id, custom_food=parent, custom_food__user=request.user
        ).delete()
    return redirect('recipe_detail', food_id=parent.id)


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
    return redirect('recipe_detail', food_id=parent.id)


@login_required
def save_recipe_final_values(request, food_id):
    """Manually correct the parent recipe's own final per-100g core
    nutrient fields. Edits ONLY those typed fields - never touches
    ingredients and never triggers recalculation, so it can't be silently
    overwritten by anything except an explicit later 'Recalculate' action."""
    parent = get_object_or_404(CustomFood, pk=food_id, user=request.user)
    if request.method != "POST":
        return redirect('recipe_detail', food_id=parent.id)

    form = FinalNutrientValuesForm(request.POST, instance=parent)
    if form.is_valid():
        form.save()
        return redirect('recipe_detail', food_id=parent.id)

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
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else now().date()
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
    
    date_str = request.GET.get('date', now().date().strftime('%Y-%m-%d'))
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

    if request.method == "POST":
        form = MealEntryForm(request.POST, instance=entry)
        if form.is_valid():
            form.save()
            return redirect(f'/food-diary/?date={entry.daily_log.date.strftime("%Y-%m-%d")}')
    else:
        form = MealEntryForm(instance=entry)
    
    return render(request, "nutrition/edit_meal_entry.html", {"form": form, "entry": entry})


@login_required
def delete_meal_entry(request, entry_id):
    """Delete a meal entry. Scoped to request.user so one user's entry ID
    cannot be read or mutated by another user."""
    entry = get_object_or_404(MealEntry, pk=entry_id, user=request.user)
    date = entry.daily_log.date
    
    if request.method == "POST":
        entry.delete()
        return redirect(f'/food-diary/?date={date.strftime("%Y-%m-%d")}')
    
    return render(request, "nutrition/delete_meal_entry.html", {"entry": entry})


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
        'default_portion': float(food.default_portion_g) if food.default_portion_g else 100,
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
        foods = foods.filter(
            Q(name__icontains=query) | Q(brand__icontains=query)
        )
    
    results = [format_custom_food_for_frontend(food) for food in foods[:30]]

    return JsonResponse({
        'results': results,
        'count': len(results)
    })


def search_fineli_api(request):
    """Search Fineli database (local only)."""
    query = request.GET.get('q', '').strip()
    if len(query) < 2:
        favorites = FineliFood.objects.filter(usage_count__gt=0).order_by('-usage_count', '-last_used')[:10]
        results = [format_food_for_frontend(food, is_favorite=True) for food in favorites]
        return JsonResponse({
            'results': results,
            'source': 'favorites',
            'count': len(results)
        })
    
    try:
        foods = FineliFood.objects.filter(
            Q(name_fi__icontains=query) |
            Q(name_sv__icontains=query) |
            Q(name_en__icontains=query)
        ).order_by('-usage_count', 'name_fi')[:20]
        
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
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else now().date()
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
            try:
                custom_food = CustomFood.objects.get(id=custom_food_id, user=request.user)
                custom_food.use_count += 1
                custom_food.save()
                source_type = 'custom'
            except CustomFood.DoesNotExist:
                pass
        elif fineli_id:
            fineli_food, created = FineliFood.objects.get_or_create(
                fineli_id=fineli_id,
                defaults={
                    'name_fi': data.get('name', ''),
                    'name_en': data.get('name_en', ''),
                    # No fallback default here: a missing key means the
                    # client didn't send a value, i.e. unknown - not 0.
                    'energy_kcal': data.get('energy_kcal'),
                    'protein_g': data.get('protein'),
                    'carbs_g': data.get('carbs'),
                    'sugar_g': data.get('sugar'),
                    'fat_g': data.get('fat'),
                    'sat_fat_g': data.get('sat_fat'),
                    'fiber_g': data.get('fiber'),
                    'salt_g': data.get('salt'),
                }
            )
            
            fineli_food.usage_count += 1
            fineli_food.last_used = now()
            fineli_food.save()
            source_type = 'fineli'
        
        portion_grams = float(data.get('portion_grams', 100))
        multiplier = portion_grams / 100.0

        def _scaled_from_json(key):
            # Missing key = unknown, not 0 - preserve None rather than
            # fabricating a scaled zero.
            value = data.get(key)
            return None if value is None else value * multiplier

        with transaction.atomic():
            entry = MealEntry.objects.create(
                daily_log=daily_log,
                user=request.user,
                fineli_food=fineli_food,
                custom_food=custom_food,
                source_type=source_type,
                food_name=data.get('name', ''),
                meal_type=data.get('meal_type', 'snack'),
                portion_grams=portion_grams,
                calories=_scaled_from_json('energy_kcal'),
                protein_g=_scaled_from_json('protein'),
                carbs_g=_scaled_from_json('carbs'),
                sugar_g=_scaled_from_json('sugar'),
                fat_g=_scaled_from_json('fat'),
                sat_fat_g=_scaled_from_json('sat_fat'),
                fiber_g=_scaled_from_json('fiber'),
                salt_g=_scaled_from_json('salt'),
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
        .select_related('fineli_food', 'custom_food').order_by('-created_at')
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
    )}
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

    total_new_days = 0
    total_updated_days = 0
    total_food_rows = 0

    owner = request.user

    for date_str, rows in rows_by_date.items():
        day_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        daily_log, created = DailyLog.objects.get_or_create(date=day_date)

        # Replace only this owner's prior bulk-import rows for this date;
        # manual / Fineli / custom-food entries, and other users' or legacy
        # (user=None) bulk-import rows, are untouched.
        daily_log.meal_entries.filter(source_type='bulk_import', user=owner).delete()

        new_entries = MealEntry.objects.bulk_create([
            MealEntry(
                daily_log=daily_log,
                user=owner,
                source_type='bulk_import',
                food_name=row['food_name'],
                meal_type=row['meal_type'],
                portion_grams=row['portion_grams'],
                calories=row['calories'],
                protein_g=row['protein_g'],
                protein_qualifier=row['protein_qualifier'],
                carbs_g=row['carbs_g'],
                carbs_qualifier=row['carbs_qualifier'],
                fat_g=row['fat_g'],
                fat_qualifier=row['fat_qualifier'],
                sat_fat_g=row['sat_fat_g'],
                sat_fat_qualifier=row['sat_fat_qualifier'],
                sugar_g=row['sugar_g'],
                sugar_qualifier=row['sugar_qualifier'],
                fiber_g=row['fiber_g'],
                fiber_qualifier=row['fiber_qualifier'],
                salt_g=row['salt_g'],
                salt_qualifier=row['salt_qualifier'],
                source_food_id=row['source_food_id'],
                source_amount=row['source_amount'],
                source_unit=row['source_unit'],
                source_ingredients_classification=row['source_ingredients_classification'],
                source_food_use_classification=row['source_food_use_classification'],
            )
            for row in rows
        ])

        MealEntryNutrient.objects.bulk_create([
            MealEntryNutrient(
                meal_entry=entry,
                nutrient_key=aux['nutrient_key'],
                value=aux['value'],
                unit=aux['unit'],
                qualifier=aux['qualifier'],
            )
            for row, entry in zip(rows, new_entries)
            for aux in row['aux_nutrients']
        ])

        daily_log.recalculate_from_meals()

        total_food_rows += len(rows)
        if created:
            total_new_days += 1
        else:
            total_updated_days += 1

    return JsonResponse({
        'success': True,
        'total_imported': total_new_days,
        'total_updated': total_updated_days,
        'files': file_results,
        'message': f'Imported {total_food_rows} food entries across {total_new_days} new day(s) and {total_updated_days} updated day(s) from {len(files)} file(s)'
    })
