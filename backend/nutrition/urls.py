from django.urls import path
from .views import (
    food_diary_view, add_custom_food, create_recipe, manage_custom_foods, edit_custom_food, delete_custom_food,
    add_meal_entry, edit_meal_entry, delete_meal_entry,
    search_fineli_api, add_fineli_entry, bulk_import_diary_csv,
    list_custom_foods_api,
    add_favorite_food, remove_favorite_food, list_favorite_foods, list_recent_foods, list_frequent_foods,
    recipe_detail_view, add_recipe_fineli_ingredient, add_recipe_custom_ingredient,
    remove_recipe_ingredient, recalculate_recipe_view, save_recipe_final_values,
)

urlpatterns = [
    # Food diary
    path("", food_diary_view, name="food_diary"),
    path("add-entry/", add_meal_entry, name="add_meal_entry"),
    path("edit-entry/<int:entry_id>/", edit_meal_entry, name="edit_meal_entry"),
    path("delete-entry/<int:entry_id>/", delete_meal_entry, name="delete_meal_entry"),
    
    # Fineli API integration
    path("api/search/", search_fineli_api, name="search_fineli_api"),
    path("api/custom-foods/", list_custom_foods_api, name="list_custom_foods_api"),
    path("api/add/", add_fineli_entry, name="add_fineli_entry"),
    path("api/bulk-import/", bulk_import_diary_csv, name="bulk_import_diary_csv"),
    path("api/favorites/add/", add_favorite_food, name="add_favorite_food"),
    path("api/favorites/remove/", remove_favorite_food, name="remove_favorite_food"),
    path("api/favorites/", list_favorite_foods, name="list_favorite_foods"),
    path("api/recent/", list_recent_foods, name="list_recent_foods"),
    path("api/frequent/", list_frequent_foods, name="list_frequent_foods"),

    # Custom foods management
    path("custom-foods/", manage_custom_foods, name="manage_custom_foods"),
    path("custom-foods/add/", add_custom_food, name="add_custom_food"),
    path("custom-foods/edit/<int:food_id>/", edit_custom_food, name="edit_custom_food"),
    path("custom-foods/delete/<int:food_id>/", delete_custom_food, name="delete_custom_food"),

    # Recipe composition
    path("custom-foods/create-recipe/", create_recipe, name="create_recipe"),
    path("custom-foods/<int:food_id>/recipe/", recipe_detail_view, name="recipe_detail"),
    path("custom-foods/<int:food_id>/recipe/add-fineli/", add_recipe_fineli_ingredient, name="add_recipe_fineli_ingredient"),
    path("custom-foods/<int:food_id>/recipe/add-custom/", add_recipe_custom_ingredient, name="add_recipe_custom_ingredient"),
    path("custom-foods/<int:food_id>/recipe/remove/<int:ingredient_id>/", remove_recipe_ingredient, name="remove_recipe_ingredient"),
    path("custom-foods/<int:food_id>/recipe/recalculate/", recalculate_recipe_view, name="recalculate_recipe"),
    path("custom-foods/<int:food_id>/recipe/save-values/", save_recipe_final_values, name="save_recipe_final_values"),
]
