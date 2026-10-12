"""Explicit, side-effecting service that materializes a recipe CustomFood's
derived nutrient values - from its CustomFoodIngredient rows - into the
parent CustomFood's own typed core fields and CustomFoodNutrient rows.

Conservative by design: the existing CustomFood schema (typed nullable core
fields, CustomFoodNutrient rows) has no way to encode partial coverage, so
only a COMPLETE recipe_aggregation result can be persisted truthfully. A
partial/none/no_ingredient_data result is persisted as unknown (None / no
row) - never as its known subtotal, and never fabricated as zero. The richer
recipe_aggregation.RecipeNutrientResult remains the place to inspect partial
coverage computationally.

Not wired to any model save()/signal - views call it explicitly.
Nested source_custom_food ingredients contribute their OWN currently
persisted per-100g values; recalculate_custom_food_recipe() does not
recursively recalculate them first. recalculate_dependent_recipes() is the
other direction: after a food changes, it updates every recipe using it.
"""
from django.db import transaction

from .models import CustomFood, CustomFoodIngredient, NUTRIENT_UNITS, replace_custom_food_nutrients
from .recipe_aggregation import normalize_to_per_100g, recipe_nutrient_result, recipe_yield_grams

# Recipe core result key -> CustomFood typed per-100g field name.
_CORE_TARGET_FIELD = {
    "calories": "energy_kcal_per_100g",
    "protein_g": "protein_per_100g",
    "carbs_g": "carbs_per_100g",
    "sugar_g": "sugar_per_100g",
    "fat_g": "fat_per_100g",
    "sat_fat_g": "sat_fat_per_100g",
    "fiber_g": "fiber_per_100g",
    "salt_g": "salt_per_100g",
}


def recalculate_custom_food_recipe(custom_food):
    """Recalculate `custom_food` from its current CustomFoodIngredient rows
    and persist only truthfully-complete results. Ingredient rows and every
    source (FineliFood/CustomFood and their nutrient children) are read-only
    inputs and are never mutated - only `custom_food`'s own core fields and
    CustomFoodNutrient rows change."""
    # Per 100 g of the finished dish when its weight is known.
    grams = recipe_yield_grams(custom_food)

    core_updates = {}
    for result_key, field_name in _CORE_TARGET_FIELD.items():
        result = recipe_nutrient_result(custom_food, result_key)
        if result.coverage == "complete":
            # Core aggregation never produces a bounded (lt) result - there
            # is no per-nutrient qualifier on FineliFood/CustomFood core
            # fields to originate one - so the exact per-100g value is the
            # single correct number to persist here.
            per100 = normalize_to_per_100g(result, grams)
            core_updates[field_name] = per100.lower_bound
        else:
            core_updates[field_name] = None

    aux_rows = []
    for nutrient_key in NUTRIENT_UNITS:
        result = recipe_nutrient_result(custom_food, nutrient_key)
        if result.coverage != "complete":
            continue
        per100 = normalize_to_per_100g(result, grams)
        if result.bound_status == "exact":
            aux_rows.append({"nutrient_key": nutrient_key, "value": per100.lower_bound, "qualifier": "exact"})
        else:  # "bounded" - only the whole-recipe upper bound is valid to persist
            aux_rows.append({"nutrient_key": nutrient_key, "value": per100.upper_bound, "qualifier": "lt"})

    with transaction.atomic():
        for field_name, value in core_updates.items():
            setattr(custom_food, field_name, value)
        custom_food.save()
        replace_custom_food_nutrients(custom_food, aux_rows)


class RecipeGraphError(Exception):
    """Raised when nested-dependency discovery finds a stored recipe graph
    that existing CustomFoodIngredient validation should have prevented
    (a cycle, or a cross-owner dependency) - defensive only. Raised before
    any recalculation write happens, so nothing is left half-updated."""


def _discover_dependencies(root):
    """Iterative (non-recursive) DFS over source_custom_food ingredient
    edges reachable from `root`, with no depth limit. Returns
    (nodes, order, has_ingredients):
    - nodes: {id: CustomFood} for every discovered CustomFood, root included
    - order: ids in deterministic child-before-parent order, root last
    - has_ingredients: {id: bool} whether that CustomFood has ANY ingredient
      row (Fineli or CustomFood-sourced) - a False leaf is a manual food and
      must not be recalculated.
    Raises RecipeGraphError on a cycle or a dependency owned by someone
    other than root, without mutating anything.
    """
    root_owner = root.user_id
    nodes = {root.id: root}
    has_ingredients = {}
    WHITE, GRAY, BLACK = 0, 1, 2

    def child_custom_foods(custom_food_id):
        ingredients = list(
            CustomFoodIngredient.objects.filter(custom_food_id=custom_food_id)
            .select_related('source_custom_food')
            .order_by('source_custom_food_id')
        )
        has_ingredients[custom_food_id] = bool(ingredients)
        return [ing.source_custom_food for ing in ingredients if ing.source_custom_food_id]

    color = {root.id: GRAY}
    order = []
    stack = [(root.id, iter(child_custom_foods(root.id)))]

    while stack:
        node_id, child_iter = stack[-1]
        advanced = False
        for child in child_iter:
            if child.user_id != root_owner:
                raise RecipeGraphError(
                    f"CustomFood {child.id} is not owned by user {root_owner}; aborting recalculation."
                )
            state = color.get(child.id, WHITE)
            if state == GRAY:
                raise RecipeGraphError(f"Cycle detected in recipe graph at CustomFood {child.id}.")
            if state == BLACK:
                continue  # already fully discovered via another path
            nodes[child.id] = child
            color[child.id] = GRAY
            stack.append((child.id, iter(child_custom_foods(child.id))))
            advanced = True
            break
        if not advanced:
            color[node_id] = BLACK
            order.append(node_id)
            stack.pop()

    return nodes, order, has_ingredients


def recalculate_custom_food_recipe_with_dependencies(root):
    """Recalculate `root` and every nested ingredient-composed CustomFood
    it (transitively) depends on, child-before-parent, so each level reads
    its dependencies' freshly persisted values. Orchestration only - reuses
    recalculate_custom_food_recipe() for every actual calculation, never
    duplicating its logic.

    A dependency with zero ingredients is a manual/leaf food and is left
    untouched (its persisted values are authoritative manual data) - only
    `root` itself is always recalculated, even with zero ingredients,
    matching recalculate_custom_food_recipe()'s existing explicit semantics.
    A dependency reachable via more than one path is recalculated exactly
    once. The whole operation is one atomic unit: a defensive cycle or
    cross-owner check runs before any write, and every write shares one
    outer transaction.
    """
    with transaction.atomic():
        nodes, order, has_ingredients = _discover_dependencies(root)
        for node_id in order:
            if node_id == root.id or has_ingredients.get(node_id):
                recalculate_custom_food_recipe(nodes[node_id])


def recalculate_dependent_recipes(food):
    """Recalculate every recipe that uses `food` as an ingredient - directly
    or through other recipes - after `food`'s values changed, so recipes
    never keep stale values. A recipe is recalculated only after all of its
    changed ingredients (it reads their freshly persisted values). Returns
    the recalculated recipes in that order; one atomic unit."""
    with transaction.atomic():
        parents_of = {}
        affected = set()
        frontier = [food.id]
        while frontier:
            current = frontier.pop()
            parent_ids = set(
                CustomFoodIngredient.objects.filter(source_custom_food_id=current)
                .values_list("custom_food_id", flat=True)
            )
            parents_of[current] = parent_ids
            for parent_id in parent_ids:
                if parent_id == food.id:
                    raise RecipeGraphError(f"Cycle detected in recipe graph at CustomFood {food.id}.")
                if parent_id not in affected:
                    affected.add(parent_id)
                    frontier.append(parent_id)

        waiting_for = {recipe_id: set() for recipe_id in affected}
        for child_id, parent_ids in parents_of.items():
            for parent_id in parent_ids:
                waiting_for[parent_id].add(child_id)

        done = {food.id}
        order = []
        while len(order) < len(affected):
            ready = sorted(r for r in affected if r not in done and waiting_for[r] <= done)
            if not ready:
                raise RecipeGraphError("Cycle detected in recipe graph.")
            order.extend(ready)
            done.update(ready)

        recipes = CustomFood.objects.in_bulk(order)
        for recipe_id in order:
            recalculate_custom_food_recipe(recipes[recipe_id])
        return [recipes[recipe_id] for recipe_id in order]
