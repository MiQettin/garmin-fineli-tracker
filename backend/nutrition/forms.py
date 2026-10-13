from django import forms
from django.utils.translation import gettext
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
        elif "package_amount" in self.data:
            # Package size is optional: left blank (or cleared on edit), the
            # product has no package and is logged by weight/volume only.
            food.package_amount = None
            food.package_unit = "G"
            if food.default_portion_g is None:
                # The browser posts the hidden default_portion_g field empty
                # for a new product; the column is NOT NULL.
                food.default_portion_g = CustomFood._meta.get_field("default_portion_g").default
        elif self.cleaned_data.get("default_portion_g") is not None:
            # Older clients only submit default_portion_g.
            food.package_amount = self.cleaned_data["default_portion_g"]
            food.package_unit = "G"
        if commit:
            food.save()
        return food

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("package_amount") is None:
            return cleaned  # no package - nothing to match against the basis
        package_unit = cleaned.get("package_unit") or "G"
        nutrition_basis = cleaned.get("nutrition_basis") or "G"
        if nutrition_basis == "G" and package_unit != "G":
            self.add_error("package_unit", gettext("Nutrition values are per 100 g, so give the package size in grams "
                                                   "(e.g. 250 g), choose values per 100 ml, or leave the package size empty."))
        if nutrition_basis == "ML" and package_unit == "G":
            self.add_error("package_unit", gettext("Nutrition values are per 100 ml, so give the package size in ml, dl or l, "
                                                   "choose values per 100 g, or leave the package size empty."))
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


class RecipeSettingsForm(forms.ModelForm):
    """A recipe's name and optional yield: servings (log "1 × annos") and the
    finished dish's weight (per-100 g values are based on it)."""
    class Meta:
        model = CustomFood
        fields = ["name", "brand", "servings", "cooked_weight_g"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "brand": forms.TextInput(attrs={"class": "form-control", "placeholder": "Optional"}),
            "servings": forms.NumberInput(attrs={"class": "form-control", "min": "0.5", "step": "0.5", "inputmode": "decimal", "placeholder": "Optional"}),
            "cooked_weight_g": forms.NumberInput(attrs={"class": "form-control", "min": "1", "step": "0.01", "inputmode": "decimal", "placeholder": "Optional"}),
        }

    def clean_servings(self):
        value = self.cleaned_data.get("servings")
        if value is not None and not 0 < value <= 1000:
            raise forms.ValidationError(gettext("Servings must be more than 0."))
        return value

    def clean_cooked_weight_g(self):
        value = self.cleaned_data.get("cooked_weight_g")
        if value is not None and not 0 < value <= 100000:
            raise forms.ValidationError(gettext("Weight must be more than 0 g."))
        return value


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


NUTRIENT_ENTRY_FIELDS = ["calories", "protein_g", "carbs_g", "sugar_g", "fat_g", "sat_fat_g", "fiber_g", "salt_g"]


class MealEntryEditForm(MealEntryForm):
    """Edit a logged entry. portion_amount edits a portion-unit entry
    ("2 × iso (kpl)") by count; nutrient values follow the amount unless
    they are changed by hand."""
    portion_amount = forms.FloatField(
        required=False, min_value=0.01,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "inputmode": "decimal"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Diary CSV imports keep their own meal names (e.g. "Herkku"); the
        # entry's current meal must stay a valid choice.
        current = self.instance.meal_type
        # Meal names in the app language (the stored values stay the same).
        labels = {
            "breakfast": gettext("Breakfast"), "lunch": gettext("Lunch"), "dinner": gettext("Dinner"),
            "snack": gettext("Snack"), "evening_snack": gettext("Evening snack"),
        }
        self.fields["meal_type"].choices = [
            (value, labels.get(value, label)) for value, label in self.fields["meal_type"].choices
        ]
        choices = list(self.fields["meal_type"].choices)
        self._imported_meal = None
        if current and current not in {value for value, _ in choices}:
            self.fields["meal_type"].choices = choices + [(current, current)]
            self._imported_meal = current
        # step="any": imported values have more than two decimals (e.g. salt
        # 0.004), which step="0.01" would make the browser refuse to submit.
        self.fields["portion_grams"].widget.attrs.update({"min": "0.01", "step": "any", "inputmode": "decimal"})
        self.fields["portion_amount"].initial = self.instance.source_amount
        self.fields["portion_amount"].widget.attrs["step"] = "any"
        for name in NUTRIENT_ENTRY_FIELDS:
            self.fields[name].widget.attrs.update({"placeholder": "unknown", "step": "any", "inputmode": "decimal"})

    def _get_validation_exclusions(self):
        # The model field's own choices check would reject the imported meal
        # name too - skip it only when the entry keeps that existing name.
        exclude = super()._get_validation_exclusions()
        if self._imported_meal and self.cleaned_data.get("meal_type") == self._imported_meal:
            exclude.add("meal_type")
        return exclude


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
