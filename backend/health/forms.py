from django import forms
from django.utils.translation import gettext_lazy as _


class WeightEntryForm(forms.Form):
    """A manual weight entry, or a correction of an existing entry."""

    # <input type="date"> only accepts ISO dates - never the localized "13.10.2026".
    date = forms.DateField(label=_("Date"), widget=forms.DateInput(format="%Y-%m-%d",
                                                                   attrs={"type": "date", "class": "form-control"}))
    weight_kg = forms.FloatField(
        label=_("Weight (kg)"), min_value=20, max_value=400,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.1", "min": "20", "max": "400",
                                        "inputmode": "decimal"}),
    )
