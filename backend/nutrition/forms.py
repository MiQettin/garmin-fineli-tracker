from django import forms
from .models import CustomFood, MealEntry


class CustomFoodForm(forms.ModelForm):
    # Kept hidden for compatibility with old clients and existing records.
    default_portion_g = forms.FloatField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Older clients only submit default_portion_g. The model defaults keep
        # those requests valid while the current form always renders both.
        self.fields["package_unit"].required = False
        self.fields["nutrition_basis"].required = False

    def save(self, commit=True):
        food = super().save(commit=False)
        if self.cleaned_data.get("package_amount") is not None:
            amount = self.cleaned_data["package_amount"]
            unit = self.cleaned_data.get("package_unit") or "G"
            food.default_portion_g = {
                "G": amount,
                "ML": amount,
                "DL": amount * 100,
                "L": amount * 1000,
            }[unit]
        elif self.cleaned_data.get("default_portion_g") is not None:
            food.package_amount = self.cleaned_data["default_portion_g"]
            food.package_unit = "G"
        if commit:
            food.save()
        return food

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("package_amount") is None and cleaned.get("default_portion_g") is None:
            self.add_error("package_amount", "Package size is required.")
        package_unit = cleaned.get("package_unit") or "G"
        nutrition_basis = cleaned.get("nutrition_basis") or "G"
        if nutrition_basis == "G" and package_unit != "G":
            self.add_error("package_unit", "Nutrition per 100 g requires a package size in grams.")
        if nutrition_basis == "ML" and package_unit == "G":
            self.add_error("package_unit", "Nutrition per 100 ml requires a package size in ml, dl, or l.")
        return cleaned

    class Meta:
        model = CustomFood
        fields = [
            "name", "brand", "energy_kcal_per_100g", "protein_per_100g", "carbs_per_100g",
            "sugar_per_100g", "fat_per_100g", "sat_fat_per_100g", "fiber_per_100g",
            "salt_per_100g", "package_amount", "package_unit", "nutrition_basis",
            "default_portion_g",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g., Protein Bar Chocolate"}),
            "brand": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g., Fazer (optional)"}),
            "energy_kcal_per_100g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01", "placeholder": "e.g., 400"}),
            "protein_per_100g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01", "placeholder": "e.g., 20"}),
            "carbs_per_100g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01", "placeholder": "e.g., 50"}),
            "sugar_per_100g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01", "placeholder": "e.g., 5"}),
            "fat_per_100g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01", "placeholder": "e.g., 10"}),
            "sat_fat_per_100g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01", "placeholder": "e.g., 3"}),
            "fiber_per_100g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01", "placeholder": "e.g., 2"}),
            "salt_per_100g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01", "placeholder": "e.g., 0.5"}),
            "package_amount": forms.NumberInput(attrs={"class": "form-control", "min": "0.01", "step": "0.01", "placeholder": "e.g., 1"}),
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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            for field_name in self.Meta.fields:
                value = self.initial.get(field_name)
                if value is not None:
                    self.initial[field_name] = f"{float(value):.2f}"

    class Meta:
        model = CustomFood
        fields = [
            "energy_kcal_per_100g", "protein_per_100g", "carbs_per_100g", "sugar_per_100g",
            "fat_per_100g", "sat_fat_per_100g", "fiber_per_100g", "salt_per_100g",
        ]
        widgets = {
            "energy_kcal_per_100g": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "Unknown"}),
            "protein_per_100g": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "Unknown"}),
            "carbs_per_100g": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "Unknown"}),
            "sugar_per_100g": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "Unknown"}),
            "fat_per_100g": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "Unknown"}),
            "sat_fat_per_100g": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "Unknown"}),
            "fiber_per_100g": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "Unknown"}),
            "salt_per_100g": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "Unknown"}),
        }


class MealEntryForm(forms.ModelForm):
    class Meta:
        model = MealEntry
        fields = ["food_name", "meal_type", "portion_grams", "calories", "protein_g", "carbs_g", "sugar_g", "fat_g", "sat_fat_g", "fiber_g", "salt_g", "notes"]
        widgets = {
            "food_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Food name"}),
            "meal_type": forms.Select(attrs={"class": "form-control"}),
            "portion_grams": forms.NumberInput(attrs={"class": "form-control", "min": "1", "step": "0.01", "placeholder": "e.g., 150"}),
            "calories": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01"}),
            "protein_g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01"}),
            "carbs_g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01"}),
            "sugar_g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01"}),
            "fat_g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01"}),
            "sat_fat_g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01"}),
            "fiber_g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01"}),
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
            "portion_grams": forms.NumberInput(attrs={"class": "form-control", "min": "0.01", "step": "0.01", "placeholder": "e.g., 150"}),
        }
