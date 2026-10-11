from django import forms
from .models import CustomFood, MealEntry


class CustomFoodForm(forms.ModelForm):
    class Meta:
        model = CustomFood
        fields = ["name", "brand", "energy_kcal_per_100g", "protein_per_100g", "carbs_per_100g", "sugar_per_100g", "fat_per_100g", "sat_fat_per_100g", "fiber_per_100g", "salt_per_100g", "default_portion_g"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g., Protein Bar Chocolate"}),
            "brand": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g., Fazer (optional)"}),
            "energy_kcal_per_100g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1", "placeholder": "e.g., 400"}),
            "protein_per_100g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1", "placeholder": "e.g., 20"}),
            "carbs_per_100g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1", "placeholder": "e.g., 50"}),
            "sugar_per_100g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1", "placeholder": "e.g., 5"}),
            "fat_per_100g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1", "placeholder": "e.g., 10"}),
            "sat_fat_per_100g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1", "placeholder": "e.g., 3"}),
            "fiber_per_100g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1", "placeholder": "e.g., 2"}),
            "salt_per_100g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01", "placeholder": "e.g., 0.5"}),
            "default_portion_g": forms.NumberInput(attrs={"class": "form-control", "min": "1", "step": "1", "placeholder": "e.g., 30"}),
        }


class RecipeCreateForm(forms.ModelForm):
    """Creates the identifying shell of a recipe CustomFood only - no
    nutrient fields, so they start unknown (None), never a fabricated 0.
    Ingredients are added afterward on the recipe composition page."""
    class Meta:
        model = CustomFood
        fields = ["name", "brand"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g., My Breakfast Porridge"}),
            "brand": forms.TextInput(attrs={"class": "form-control", "placeholder": "Optional"}),
        }


class FinalNutrientValuesForm(forms.ModelForm):
    """Manual correction of a CustomFood's own final per-100g core nutrient
    fields - independent of any ingredient recalculation. Every field is
    already nullable/optional on the model, so Django's standard FloatField
    form handling already gives the required semantics: blank -> None
    (unknown), "0" -> 0.0 (known zero), any other number -> that value."""
    class Meta:
        model = CustomFood
        fields = [
            "energy_kcal_per_100g", "protein_per_100g", "carbs_per_100g", "sugar_per_100g",
            "fat_per_100g", "sat_fat_per_100g", "fiber_per_100g", "salt_per_100g",
        ]
        widgets = {
            "energy_kcal_per_100g": forms.NumberInput(attrs={"class": "form-control", "step": "0.1", "placeholder": "Unknown"}),
            "protein_per_100g": forms.NumberInput(attrs={"class": "form-control", "step": "0.1", "placeholder": "Unknown"}),
            "carbs_per_100g": forms.NumberInput(attrs={"class": "form-control", "step": "0.1", "placeholder": "Unknown"}),
            "sugar_per_100g": forms.NumberInput(attrs={"class": "form-control", "step": "0.1", "placeholder": "Unknown"}),
            "fat_per_100g": forms.NumberInput(attrs={"class": "form-control", "step": "0.1", "placeholder": "Unknown"}),
            "sat_fat_per_100g": forms.NumberInput(attrs={"class": "form-control", "step": "0.1", "placeholder": "Unknown"}),
            "fiber_per_100g": forms.NumberInput(attrs={"class": "form-control", "step": "0.1", "placeholder": "Unknown"}),
            "salt_per_100g": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "Unknown"}),
        }


class MealEntryForm(forms.ModelForm):
    class Meta:
        model = MealEntry
        fields = ["food_name", "meal_type", "portion_grams", "calories", "protein_g", "carbs_g", "sugar_g", "fat_g", "sat_fat_g", "fiber_g", "salt_g", "notes"]
        widgets = {
            "food_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Food name"}),
            "meal_type": forms.Select(attrs={"class": "form-control"}),
            "portion_grams": forms.NumberInput(attrs={"class": "form-control", "min": "1", "step": "1", "placeholder": "e.g., 150"}),
            "calories": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1"}),
            "protein_g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1"}),
            "carbs_g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1"}),
            "sugar_g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1"}),
            "fat_g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1"}),
            "sat_fat_g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1"}),
            "fiber_g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1"}),
            "salt_g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": "2", "placeholder": "Optional notes"}),
        }


class QuickMealEntryForm(forms.ModelForm):
    """Simplified form for adding from custom foods."""
    custom_food_id = forms.IntegerField(widget=forms.HiddenInput(), required=False)
    
    class Meta:
        model = MealEntry
        fields = ["meal_type", "portion_grams"]
        widgets = {
            "meal_type": forms.Select(attrs={"class": "form-control"}),
            "portion_grams": forms.NumberInput(attrs={"class": "form-control", "min": "1", "step": "1", "placeholder": "e.g., 150"}),
        }
