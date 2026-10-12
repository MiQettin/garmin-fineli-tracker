from django import forms
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
