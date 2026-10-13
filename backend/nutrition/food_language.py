"""Which language Fineli food names and portion units are shown in.

Food names follow the *food language*: Finnish when the app is in Finnish,
the user's choice (Finnish or English) when the app is in English - see
UserSettings.effective_food_language(). Portion unit labels follow the app
language. Nothing stored changes: names and labels are picked when shown.

core.middleware.UserLanguageMiddleware sets the food language for each
request; outside a request (tests, scripts) it defaults to Finnish.
"""
from contextvars import ContextVar

from django.utils import translation

_food_language = ContextVar("food_language", default="fi")


def set_food_language(language):
    return _food_language.set(language)


def reset_food_language(token):
    _food_language.reset(token)


def food_language():
    return _food_language.get()


def sentence_case(name):
    """Fineli's names are upper case; "OATS, ROLLED, OAT FLAKES" reads better
    as "Oats, rolled, oat flakes"."""
    lowered = name.lower()
    return lowered[:1].upper() + lowered[1:]


def fineli_name(food, language=None):
    """A FineliFood's name in the food language (Finnish when there is no
    English name)."""
    language = language or food_language()
    if language == "en" and food.name_en:
        return sentence_case(food.name_en)
    return food.name_fi


def name_field(language=None):
    """The FineliFood name field searched and ranked in the food language."""
    return "name_en" if (language or food_language()) == "en" else "name_fi"


# Portion unit labels per app language. Metric only: g, ml, dl, l and the
# metric spoon measures (rkl 15 ml, tl 5 ml) Fineli uses.
UNIT_LABELS = {
    "fi": {
        "G": "g",
        "KPL_S": "pieni (kpl)",
        "KPL_M": "keskikokoinen (kpl)",
        "KPL_L": "iso (kpl)",
        "KPL_VALM": "valmistajan kpl",
        "PORTS": "pieni annos",
        "PORTM": "keskikokoinen annos",
        "PORTL": "iso annos",
        "DL": "dl",
        "RKL": "rkl",
        "TL": "tl",
        "PKG": "pakkaus",
        "ML": "ml",
        "L": "l",
        "SERV": "annos",
    },
    "en": {
        "G": "g",
        "KPL_S": "small piece",
        "KPL_M": "medium piece",
        "KPL_L": "large piece",
        "KPL_VALM": "piece (manufacturer)",
        "PORTS": "small portion",
        "PORTM": "medium portion",
        "PORTL": "large portion",
        "DL": "dl",
        "RKL": "tablespoon (15 ml)",
        "TL": "teaspoon (5 ml)",
        "PKG": "package",
        "ML": "ml",
        "L": "l",
        "SERV": "serving",
    },
}


def unit_label(code, language=None):
    """Label of a portion unit code in the app language."""
    language = (language or translation.get_language() or "en")[:2]
    labels = UNIT_LABELS.get(language, UNIT_LABELS["en"])
    return labels.get(code, code)
