from django import forms
from django.utils.translation import gettext
from .models import UserSettings


class UserSettingsForm(forms.ModelForm):
    class Meta:
        model = UserSettings
        fields = [
            "weight_kg",
            "height_cm",
            "age",
            "sex",
            "activity_level",
            "fitness_goal",
            "custom_goal_kcal",
            "use_auto_thresholds",
            "min_daily_calories",
            "max_daily_calories",
            "max_daily_deficit",
            "garmin_calorie_adjustment",
            "protein_goal_g",
            "carbs_goal_g",
            "fat_goal_g",
            "week_start_day",
            "language",
            "food_language",
        ]
        widgets = {
            "weight_kg": forms.NumberInput(attrs={"class": "form-control", "min": "30", "max": "300", "step": "0.1"}),
            "height_cm": forms.NumberInput(attrs={"class": "form-control", "min": "100", "max": "250", "step": "0.1"}),
            "age": forms.NumberInput(attrs={"class": "form-control", "min": "10", "max": "120"}),
            "sex": forms.Select(attrs={"class": "form-control"}),
            "activity_level": forms.Select(attrs={"class": "form-control"}),
            "fitness_goal": forms.Select(attrs={"class": "form-control"}),
            "custom_goal_kcal": forms.NumberInput(attrs={"class": "form-control", "min": "-2000", "max": "2000", "step": "1"}),
            "use_auto_thresholds": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "min_daily_calories": forms.NumberInput(attrs={"class": "form-control", "min": "800", "max": "5000"}),
            "max_daily_calories": forms.NumberInput(attrs={"class": "form-control", "min": "1000", "max": "6000"}),
            "max_daily_deficit": forms.NumberInput(attrs={"class": "form-control", "min": "0", "max": "2000", "step": "1"}),
            "garmin_calorie_adjustment": forms.NumberInput(attrs={"class": "form-control", "min": "-5000", "max": "5000", "step": "1"}),
            "protein_goal_g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1"}),
            "carbs_goal_g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1"}),
            "fat_goal_g": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1"}),
            "week_start_day": forms.Select(attrs={"class": "form-control"}),
            "language": forms.Select(attrs={"class": "form-select"}),
            "food_language": forms.Select(attrs={"class": "form-select"}),
        }

    def clean_custom_goal_kcal(self):
        value = self.cleaned_data.get("custom_goal_kcal")
        if value is not None and not -2000 <= value <= 2000:
            raise forms.ValidationError("Use a value between -2000 and +2000 kcal/day.")
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("fitness_goal") == "custom" and cleaned.get("custom_goal_kcal") is None:
            self.add_error("custom_goal_kcal", "Enter your own daily goal (kcal/day) for a Custom goal.")
        return cleaned

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Older forms don't send the language fields - keep the saved value.
        for name in ("language", "food_language"):
            self.fields[name].required = False
        # Choice labels in the app language (translated here, not in the model,
        # whose choices would otherwise need a migration).
        self.fields["activity_level"].choices = [
            (1.2, gettext("Sedentary (little or no exercise)")),
            (1.375, gettext("Lightly active (1-3 days/week)")),
            (1.55, gettext("Moderately active (3-5 days/week)")),
            (1.725, gettext("Very active (6-7 days/week)")),
            (1.9, gettext("Extra active (athlete/physical job)")),
        ]
        self.fields["fitness_goal"].choices = [
            ("aggressive_cut", gettext("Aggressive cut (-500 kcal) - significant fat to lose")),
            ("moderate_cut", gettext("Moderate cut (-300 kcal) - standard fat loss")),
            ("mild_cut", gettext("Mild cut (-200 kcal) - lean/active individuals")),
            ("maintain", gettext("Maintenance (0 kcal) - maintain current weight")),
            ("lean_bulk", gettext("Lean bulk (+250 kcal) - slow muscle gain")),
            ("bulk", gettext("Bulk (+400 kcal) - faster muscle gain")),
            ("custom", gettext("Custom - set your own kcal/day")),
        ]
        self.fields["food_language"].choices = [("fi", gettext("Finnish (Fineli's Finnish names)")),
                                                ("en", gettext("English (Fineli's English names)"))]

    def clean_language(self):
        return self.cleaned_data.get("language") or self.instance.language

    def clean_food_language(self):
        return self.cleaned_data.get("food_language") or self.instance.food_language
