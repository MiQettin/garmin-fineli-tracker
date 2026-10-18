"""Diary meal names.

MealEntry.meal_type is free text: one of the 5 standard keys below, or any
name the user chose (or a diary CSV import brought in, e.g. "Herkku").
Nothing here rewrites stored values - the standard keys are only translated
for display, and a typed name that matches a standard meal's label (in any
app language) is stored as that meal's key.
"""
from django.conf import settings
from django.utils import translation
from django.utils.translation import gettext_lazy

MEAL_NAME_MAX_LENGTH = 50

# key, label (lazy: translated at display time) - in day order,
# like the Fineli diary's own meals (aamiainen, lounas, välipala,
# päivällinen, illallinen; a diary CSV's "illallinen" is evening_snack).
STANDARD_MEALS = [
    ('breakfast', gettext_lazy('Breakfast')),
    ('lunch', gettext_lazy('Lunch')),
    ('snack', gettext_lazy('Snack')),
    ('dinner', gettext_lazy('Dinner')),
    ('evening_snack', gettext_lazy('Supper')),
]
STANDARD_MEAL_KEYS = [key for key, _ in STANDARD_MEALS]
_LABELS = dict(STANDARD_MEALS)


def meal_label(meal_type):
    """The meal's name in the app language; own names are shown as typed."""
    if meal_type in _LABELS:
        return str(_LABELS[meal_type])
    return meal_type


def _standard_lookup():
    """casefolded key or label (every app language) -> standard key."""
    lookup = {key: key for key in STANDARD_MEAL_KEYS}
    for code, _ in settings.LANGUAGES:
        with translation.override(code):
            for key, label in STANDARD_MEALS:
                lookup[str(label).casefold()] = key
    return lookup


def standard_meal_key(name):
    """The standard key a name stands for ("Aamiainen" -> "breakfast"), or None."""
    return _standard_lookup().get(' '.join(str(name or '').split()).casefold())


def normalize_meal_name(name):
    """Clean a meal name from a form or the diary page.

    Returns the value to store: a standard key, or the name with extra
    whitespace removed. None when the name is empty.
    """
    name = ' '.join(str(name or '').split())
    if not name:
        return None
    return standard_meal_key(name) or name


def meal_group_key(meal_type):
    """Diary section an entry belongs to. A stored name that matches a
    standard meal (e.g. an imported "Lounas") shares that meal's section;
    the stored value itself is not changed."""
    return standard_meal_key(meal_type) or meal_type


def meal_options():
    """The standard meals' names in the app language, for the meal field's
    suggestions. Own names are typed freely."""
    return [meal_label(key) for key in STANDARD_MEAL_KEYS]
