"""Daily reference intakes of vitamins and minerals for adults, used for the
"% of the day's recommendation" in the diary's micronutrient panel.

Source: Kestävää terveyttä ruoasta - kansalliset ravitsemussuositukset 2024
(Valtion ravitsemusneuvottelukunta / THL, Ohjaus 10/2024), appendices
10 a-d, which are based on the Nordic Nutrition Recommendations 2023. The
values are recommended intakes (RI) or, where no RI is set, adequate
intakes (AI). Sodium has an upper target instead: under 5 g of salt a day
(1 g of sodium = 2.54 g of salt).

These are population values for healthy adults, not for athletes with a
heavy training load. Nutrients without a daily reference (omega-3, EPA, DHA,
omega-6, trans fat, cholesterol) get no percentage.
"""

# Adult age groups of the tables: 18-24, 25-50, 51-70, over 70.
_AGE_GROUPS = (24, 50, 70)


def _age_group(age):
    if age is None:
        return 1  # 25-50 when the age is not set
    for index, upper in enumerate(_AGE_GROUPS):
        if age <= upper:
            return index
    return 3


# key -> {sex: value or [18-24, 25-50, 51-70, >70]}; a plain value is the
# same for both sexes and all adult ages. Units as in NUTRIENT_UNITS.
REFERENCE_INTAKES = {
    # Vitamins (appendices 10 a, 10 b)
    "vitamin_a_rae": {"F": [700, 700, 700, 650], "M": [800, 800, 800, 750]},  # µg RE
    "vitamin_d": 10,  # µg; 20 µg from age 75 (see reference_intake)
    "vitamin_e": {"F": [10, 10, 9, 9], "M": 11},  # mg α-TE
    "vitamin_k": {"F": [65, 65, 60, 60], "M": [75, 75, 70, 70]},  # µg
    "vitamin_c": {"F": 95, "M": 110},  # mg
    "vitamin_b12": 4,  # µg
    "folate": 330,  # µg
    "riboflavin": 1.6,  # mg
    "vitamin_b6": {"F": 1.6, "M": [1.8, 1.8, 1.8, 1.7]},  # mg
    # Minerals (appendices 10 c, 10 d)
    "calcium": [1000, 950, 950, 950],  # mg
    "iron": {"F": [15, 15, 8, 7], "M": 9},  # mg; women 51+: 15 mg if menstruating
    "zinc": {"F": [9.7, 9.7, 9.5, 9.3], "M": [12.7, 12.7, 12.4, 12.1]},  # mg
    "phosphorus": [550, 520, 520, 520],  # mg
    "potassium": 3500,  # mg
    "magnesium": {"F": 300, "M": 350},  # mg
    "iodine": 150,  # µg
    "selenium": {"F": 75, "M": [90, 90, 90, 85]},  # µg
}

# Recommended per megajoule of the day's energy intake.
PER_MJ_INTAKES = {
    "thiamine": 0.1,  # mg/MJ
    "niacin_equivalent": 1.6,  # NE (mg)/MJ
}

# Upper targets: the percentage tells how much of the maximum is used.
UPPER_TARGETS = {
    "sodium": 5000 / 2.54,  # mg: under 5 g of salt a day
}

KCAL_PER_MJ = 239.006


def reference_intake(key, sex, age=None, energy_kcal=None):
    """The day's reference for nutrient `key` as (value, is_upper_target),
    or None when there is none - or no sex is set, since most values differ
    by sex. Per-MJ values need the day's energy intake."""
    if sex not in ("F", "M"):
        return None
    if key in UPPER_TARGETS:
        return UPPER_TARGETS[key], True
    if key in PER_MJ_INTAKES:
        if not energy_kcal or energy_kcal <= 0:
            return None
        return PER_MJ_INTAKES[key] * energy_kcal / KCAL_PER_MJ, False
    value = REFERENCE_INTAKES.get(key)
    if value is None:
        return None
    if isinstance(value, dict):
        value = value[sex]
    if isinstance(value, list):
        value = value[_age_group(age)]
    if key == "vitamin_d" and age is not None and age >= 75:
        value = 20
    return value, False


def percent_of_reference(amount, key, sex, age=None, energy_kcal=None):
    """`amount` as a whole-number percentage of the day's reference, with
    whether the reference is an upper target; None without a reference."""
    reference = reference_intake(key, sex, age, energy_kcal)
    if reference is None:
        return None
    value, is_upper = reference
    return round(100 * amount / value), is_upper
