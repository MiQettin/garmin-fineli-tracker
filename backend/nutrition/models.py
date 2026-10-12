from django.conf import settings
from django.db import models

# Source-value qualifier: the Fineli diary CSV reports some numeric values as
# a below-detection-limit threshold ("<0.1") rather than an exact number.
# Only "<" is proven in real export data - no other comparison operator.
# Shared by MealEntry's per-nutrient qualifier fields and MealEntryNutrient.
NUTRIENT_QUALIFIER_CHOICES = [("exact", "Exact"), ("lt", "Less than")]


def _scaled(per_100g_value, multiplier):
    """Scale a per-100g source value by portion multiplier, preserving None
    (unknown) instead of crashing or fabricating a 0."""
    return per_100g_value * multiplier if per_100g_value is not None else None


class FineliFood(models.Model):
    """Cache for Fineli API food data."""
    fineli_id = models.IntegerField(unique=True, help_text="Fineli food ID from API")
    
    # Names in multiple languages
    name_fi = models.CharField(max_length=300, help_text="Finnish name")
    name_sv = models.CharField(max_length=300, blank=True, help_text="Swedish name")
    name_en = models.CharField(max_length=300, blank=True, help_text="English name")
    
    # Nutritional values per 100g. None = not reported for this food in the
    # Fineli source dataset (unknown); 0 = source explicitly reported zero.
    energy_kcal = models.FloatField(null=True, blank=True, help_text="Energy in kcal per 100g (None if unavailable)")
    protein_g = models.FloatField(null=True, blank=True, help_text="Protein per 100g (None if unavailable)")
    carbs_g = models.FloatField(null=True, blank=True, help_text="Carbohydrates per 100g (None if unavailable)")
    sugar_g = models.FloatField(null=True, blank=True, help_text="Sugars per 100g (None if unavailable)")
    fat_g = models.FloatField(null=True, blank=True, help_text="Fat per 100g (None if unavailable)")
    sat_fat_g = models.FloatField(null=True, blank=True, help_text="Saturated fat per 100g (None if unavailable)")
    fiber_g = models.FloatField(null=True, blank=True, help_text="Fiber per 100g (None if unavailable)")
    salt_g = models.FloatField(null=True, blank=True, help_text="Salt per 100g (None if unavailable)")
    
    # Metadata
    last_fetched = models.DateTimeField(auto_now=True, help_text="Last time data was fetched from API")
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Usage tracking for favorites
    usage_count = models.IntegerField(default=0, help_text="Number of times this food has been added")
    last_used = models.DateTimeField(null=True, blank=True, help_text="Last time this food was used")
    
    class Meta:
        ordering = ['name_fi']
        verbose_name = "Fineli Food"
        verbose_name_plural = "Fineli Foods"
        db_table = 'tracker_finelifood'  # Use existing table
    
    def __str__(self):
        return f"{self.name_fi} (ID: {self.fineli_id})"


# Fineli portion unit codes (foodunit_FI.csv THSCODE -> DESCRIPT), in the
# order the portion picker lists them. "G" is not a Fineli add-unit; it is
# the diary CSV's code for a plain gram amount.
FINELI_UNIT_LABELS = {
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
}
_FINELI_UNIT_ORDER = {code: i for i, code in enumerate(FINELI_UNIT_LABELS)}

# Units a MealEntry portion can be logged in: Fineli's, plus "PKG" - one
# package of a custom product (CustomFood.default_portion_g grams) - the
# volume units of per-100 ml custom products, and "SERV" - one serving of a
# recipe (its yield / servings grams).
PORTION_UNIT_LABELS = {**FINELI_UNIT_LABELS, "PKG": "pakkaus", "ML": "ml", "L": "l", "SERV": "annos"}


def fineli_unit_sort_key(unit_code):
    """Picker order for a unit code; unknown codes sort last."""
    return (_FINELI_UNIT_ORDER.get(unit_code, len(_FINELI_UNIT_ORDER)), unit_code)


class FineliFoodUnit(models.Model):
    """One Fineli portion size for a food (foodaddunit.csv), e.g. peeled
    banana KPL_L = 150 g. Imported by import_fineli_csv; replaced wholesale
    per food on re-import."""
    fineli_food = models.ForeignKey(FineliFood, on_delete=models.CASCADE, related_name="units")
    unit_code = models.CharField(max_length=20, help_text="Fineli unit code, e.g. KPL_M, PORTM, DL")
    mass_g = models.FloatField(help_text="Grams in one unit of this food")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["fineli_food", "unit_code"], name="unique_fineli_food_unit"),
        ]

    @property
    def label(self):
        return FINELI_UNIT_LABELS.get(self.unit_code, self.unit_code)

    def __str__(self):
        return f"{self.fineli_food_id}:{self.unit_code}={self.mass_g}g"


class CustomFood(models.Model):
    """User's custom food items (products not in Fineli)."""
    PACKAGE_UNIT_CHOICES = [
        ("G", "g"),
        ("ML", "ml"),
        ("DL", "dl"),
        ("L", "l"),
    ]
    NUTRITION_BASIS_CHOICES = [
        ("G", "100 g"),
        ("ML", "100 ml"),
    ]
    KIND_PRODUCT = "product"
    KIND_RECIPE = "recipe"
    KIND_CHOICES = [
        (KIND_PRODUCT, "Product"),
        (KIND_RECIPE, "Recipe"),
    ]
    # None means legacy/unowned (created before per-user ownership existed) -
    # never fabricate a user for these. Not exposed in CustomFoodForm, so it
    # can only be set from request.user server-side, never client input.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='custom_foods',
        help_text="Owner of this custom food (None for legacy/unowned rows)",
    )
    name = models.CharField(max_length=200, help_text="Food name (e.g., 'Fazer Protein Bar Chocolate')")
    brand = models.CharField(max_length=100, blank=True, help_text="Brand name (optional)")
    
    # Nutrition per 100g (standardized like Fineli). None = not supplied by
    # the user (unknown); 0 = user explicitly entered zero.
    energy_kcal_per_100g = models.FloatField(null=True, blank=True, help_text="Energy in kcal per 100g (None if not supplied)")
    protein_per_100g = models.FloatField(null=True, blank=True, help_text="Protein in grams per 100g (None if not supplied)")
    carbs_per_100g = models.FloatField(null=True, blank=True, help_text="Carbs in grams per 100g (None if not supplied)")
    sugar_per_100g = models.FloatField(null=True, blank=True, help_text="Sugar in grams per 100g (None if not supplied)")
    fat_per_100g = models.FloatField(null=True, blank=True, help_text="Fat in grams per 100g (None if not supplied)")
    sat_fat_per_100g = models.FloatField(null=True, blank=True, help_text="Saturated fat in grams per 100g (None if not supplied)")
    fiber_per_100g = models.FloatField(null=True, blank=True, help_text="Fiber in grams per 100g (None if not supplied)")
    salt_per_100g = models.FloatField(null=True, blank=True, help_text="Salt in grams per 100g (None if not supplied)")
    
    # Default serving
    default_portion_g = models.FloatField(default=100, help_text="Default portion size in grams")
    package_amount = models.FloatField(
        null=True, blank=True, help_text="Package size in package_unit"
    )
    package_unit = models.CharField(
        max_length=2, choices=PACKAGE_UNIT_CHOICES, default="G",
        help_text="Unit for the package size",
    )
    nutrition_basis = models.CharField(
        max_length=2, choices=NUTRITION_BASIS_CHOICES, default="G",
        help_text="Whether nutrition values are per 100 g or per 100 ml",
    )

    # Product (values from the label) or recipe (values from ingredients).
    kind = models.CharField(
        max_length=10, choices=KIND_CHOICES, default=KIND_PRODUCT,
        help_text="Product (label values) or recipe (built from ingredients)",
    )
    # Recipe yield - both optional. servings lets the diary log "1 × annos"
    # (yield / servings grams) besides grams; cooked_weight_g is the weight
    # of the finished dish, so per-100 g values account for water lost or
    # gained in cooking (falls back to the ingredients' total mass).
    servings = models.FloatField(
        null=True, blank=True, help_text="How many servings the recipe makes (optional)",
    )
    cooked_weight_g = models.FloatField(
        null=True, blank=True, help_text="Weight of the finished dish in grams (optional)",
    )
    
    # Usage tracking
    use_count = models.IntegerField(default=0, help_text="How many times this food has been logged")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-use_count', 'name']
        verbose_name = "Custom Food"
        verbose_name_plural = "Custom Foods"
        db_table = 'tracker_customfood'  # Use existing table
    
    def __str__(self):
        if self.brand:
            return f"{self.brand} {self.name}"
        return self.name


class MealEntry(models.Model):
    """Individual food entry for a specific day."""
    daily_log = models.ForeignKey(
        'core.DailyLog',
        on_delete=models.CASCADE,
        related_name='meal_entries'
    )

    # Owner of this diary entry. Null means legacy/unowned (rows written
    # before per-user diary ownership existed, or created by an anonymous
    # request) - never fabricate a user for these. DailyLog itself stays
    # global/shared (it also carries non-nutrition Garmin data consumed by
    # Running Analytics), so MealEntry.user - not DailyLog.user - is the
    # ownership root for the diary.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='diary_entries',
        help_text="Owner of this diary entry (None for legacy/unowned rows)",
    )

    # Source tracking
    fineli_food = models.ForeignKey(
        FineliFood, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        help_text="Link to Fineli food (if from API)"
    )
    custom_food = models.ForeignKey(
        CustomFood, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        help_text="Link to custom food (if user-created)"
    )
    
    SOURCE_CHOICES = [
        ('fineli', 'Fineli API'),
        ('custom', 'Custom Food'),
        ('manual', 'Manual Entry'),
        ('bulk_import', 'Bulk CSV Import'),
    ]
    source_type = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='manual', help_text="Where the food data came from")
    
    # Entry details
    food_name = models.CharField(max_length=200, help_text="Food name (copied from source or manual entry)")
    portion_grams = models.FloatField(default=100, help_text="Portion size in grams")

    # Original Fineli diary CSV row provenance (bulk_import only). Independent
    # of fineli_food/custom_food: not a resolvable relation, just the source
    # identity/quantity as originally recorded. Null/blank means unknown, not
    # zero - foodid=0 (custom/manual recipe rows in the Fineli diary) must be
    # preserved as an explicit 0.
    source_food_id = models.IntegerField(
        null=True, blank=True, help_text="Original Fineli diary CSV 'foodid' (0 is a valid explicit value, not unknown)"
    )
    # Logged portion as quantity + Fineli unit code (e.g. 1 x KPL_L). Set by
    # the diary CSV import and by the Add Food dialog for Fineli foods;
    # portion_grams stays the canonical amount. Blank = no portion recorded.
    source_amount = models.FloatField(
        null=True, blank=True, help_text="Logged quantity in source_unit (distinct from portion_grams)"
    )
    source_unit = models.CharField(
        max_length=20, blank=True, help_text="Fineli portion unit code of source_amount (e.g. G, DL, RKL, KPL_M, PORTM)"
    )
    source_ingredients_classification = models.CharField(
        max_length=200, blank=True, help_text="Original Fineli diary CSV 'ingredients (classification)' taxonomy label"
    )
    source_food_use_classification = models.CharField(
        max_length=200, blank=True, help_text="Original Fineli diary CSV 'food use (classification)' taxonomy label"
    )

    MEAL_TYPE_CHOICES = [
        ('breakfast', 'Breakfast'),
        ('lunch', 'Lunch'),
        ('dinner', 'Dinner'),
        ('snack', 'Snack'),
        ('evening_snack', 'Evening Snack'),
    ]
    # choices covers the manual "Add Food" picker; bulk CSV import stores the
    # diary's own meal-slot name verbatim (e.g. "Herkku"), which is not
    # restricted to this list - see parse_food_rows_from_content.
    meal_type = models.CharField(max_length=50, choices=MEAL_TYPE_CHOICES, default='snack', help_text="Type of meal")
    
    # Nutrition (calculated from source or entered manually). None = unknown/
    # not supplied by the source; 0 = source explicitly reported zero. Do not
    # collapse these - see aggregation.py for why that distinction matters.
    calories = models.FloatField(null=True, blank=True, help_text="Total calories for this entry (None if unknown/not supplied)")
    protein_g = models.FloatField(null=True, blank=True, help_text="Protein content in grams (None if unknown/not supplied)")
    carbs_g = models.FloatField(null=True, blank=True, help_text="Carbohydrate content in grams (None if unknown/not supplied)")
    sugar_g = models.FloatField(null=True, blank=True, help_text="Sugar content in grams (None if unknown/not supplied)")
    fat_g = models.FloatField(null=True, blank=True, help_text="Fat content in grams (None if unknown/not supplied)")
    sat_fat_g = models.FloatField(null=True, blank=True, help_text="Saturated fat content in grams (None if unknown/not supplied)")
    fiber_g = models.FloatField(null=True, blank=True, help_text="Fiber content in grams (None if unknown/not supplied)")
    salt_g = models.FloatField(null=True, blank=True, help_text="Salt content in grams (None if unknown/not supplied)")

    # Source-value qualifiers for the six typed core fields proven to carry
    # Fineli diary "<x" (below-detection-limit) values. 'exact' for a
    # reported number, 'lt' when the source gave a threshold like '<0.1'.
    # fat_g/carbs_g/... remain the sole canonical numeric owners; these only
    # annotate how that number was reported.
    protein_qualifier = models.CharField(max_length=10, choices=NUTRIENT_QUALIFIER_CHOICES, default="exact")
    carbs_qualifier = models.CharField(max_length=10, choices=NUTRIENT_QUALIFIER_CHOICES, default="exact")
    sugar_qualifier = models.CharField(max_length=10, choices=NUTRIENT_QUALIFIER_CHOICES, default="exact")
    fat_qualifier = models.CharField(max_length=10, choices=NUTRIENT_QUALIFIER_CHOICES, default="exact")
    sat_fat_qualifier = models.CharField(max_length=10, choices=NUTRIENT_QUALIFIER_CHOICES, default="exact")
    fiber_qualifier = models.CharField(max_length=10, choices=NUTRIENT_QUALIFIER_CHOICES, default="exact")
    salt_qualifier = models.CharField(max_length=10, choices=NUTRIENT_QUALIFIER_CHOICES, default="exact")

    notes = models.TextField(blank=True, help_text="Optional notes about this meal")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['meal_type', '-created_at']
        verbose_name = "Meal Entry"
        verbose_name_plural = "Meal Entries"
        db_table = 'tracker_mealentry'  # Use existing table
    
    def save(self, *args, **kwargs):
        # Calculate nutrition from source (Fineli or Custom). A None source
        # value means unknown and must stay None, not become a fabricated 0 -
        # "source_value or 0" would collapse that distinction, so use an
        # explicit is-None check per field instead.
        if self.fineli_food and not self.calories:
            self.source_type = 'fineli'
            self.food_name = self.fineli_food.name_fi
            multiplier = self.portion_grams / 100  # Fineli data is per 100g
            self.calories = _scaled(self.fineli_food.energy_kcal, multiplier)
            self.protein_g = _scaled(self.fineli_food.protein_g, multiplier)
            self.carbs_g = _scaled(self.fineli_food.carbs_g, multiplier)
            self.sugar_g = _scaled(self.fineli_food.sugar_g, multiplier)
            self.fat_g = _scaled(self.fineli_food.fat_g, multiplier)
            self.sat_fat_g = _scaled(self.fineli_food.sat_fat_g, multiplier)
            self.fiber_g = _scaled(self.fineli_food.fiber_g, multiplier)
            self.salt_g = _scaled(self.fineli_food.salt_g, multiplier)
        elif self.custom_food and not self.calories:
            self.source_type = 'custom'
            self.food_name = str(self.custom_food)
            multiplier = self.portion_grams / 100  # Custom data is per 100g
            self.calories = _scaled(self.custom_food.energy_kcal_per_100g, multiplier)
            self.protein_g = _scaled(self.custom_food.protein_per_100g, multiplier)
            self.carbs_g = _scaled(self.custom_food.carbs_per_100g, multiplier)
            self.sugar_g = _scaled(self.custom_food.sugar_per_100g, multiplier)
            self.fat_g = _scaled(self.custom_food.fat_per_100g, multiplier)
            self.sat_fat_g = _scaled(self.custom_food.sat_fat_per_100g, multiplier)
            self.fiber_g = _scaled(self.custom_food.fiber_per_100g, multiplier)
            self.salt_g = _scaled(self.custom_food.salt_per_100g, multiplier)
            
            # Increment use count
            self.custom_food.use_count += 1
            self.custom_food.save()
        
        super().save(*args, **kwargs)
        
        # Recalculate daily totals
        self.daily_log.recalculate_from_meals()
    
    def delete(self, *args, **kwargs):
        daily_log = self.daily_log
        super().delete(*args, **kwargs)
        daily_log.recalculate_from_meals()
    
    @property
    def portion_label(self):
        """"1 × iso (kpl)" for a logged unit portion; "" for plain grams or
        when no portion was recorded."""
        if not self.source_unit or self.source_unit == "G" or self.source_amount is None:
            return ""
        unit = PORTION_UNIT_LABELS.get(self.source_unit, self.source_unit)
        return f"{self.source_amount:g} × {unit}"

    def __str__(self):
        return f"{self.food_name} ({self.portion_grams}g) - {self.daily_log.date}"


# Canonical unit for each auxiliary nutrient key. This is the single source of
# truth: nutrient_key alone determines the unit a MealEntryNutrient must use.
NUTRIENT_UNITS = {
    # Carbohydrate / fiber detail
    "alcohol": "g",
    "organic_acids": "g",
    "sugar_alcohols": "g",
    "fructose": "g",
    "galactose": "g",
    "glucose": "g",
    "lactose": "g",
    "maltose": "g",
    "sucrose": "g",
    "starch": "g",
    "insoluble_fiber": "g",
    "soluble_noncellulose_polysaccharide": "g",
    # Fat composition
    "fatty_acids_total": "g",
    "pufa": "g",
    "mufa_cis": "g",
    "trans_fat": "g",
    "omega3": "g",
    "omega6": "g",
    "linoleic_acid": "mg",
    "ala": "mg",
    "epa": "mg",
    "dha": "mg",
    "cholesterol": "mg",
    "sterols": "mg",
    # Minerals
    "calcium": "mg",
    "iron": "mg",
    "iodine": "ug",
    "potassium": "mg",
    "magnesium": "mg",
    "sodium": "mg",
    "phosphorus": "mg",
    "selenium": "ug",
    "zinc": "mg",
    # Other
    "tryptophan": "mg",
    # Vitamins
    "folate": "ug",
    "niacin_equivalent": "mg",
    "niacin": "mg",
    "vitamin_b6": "mg",
    "riboflavin": "mg",
    "thiamine": "mg",
    "vitamin_a_rae": "ug",
    "carotenoids": "ug",
    "vitamin_b12": "ug",
    "vitamin_c": "mg",
    "vitamin_d": "ug",
    "vitamin_e": "mg",
    "vitamin_k": "ug",
}

NUTRIENT_KEY_CHOICES = [(key, key) for key in NUTRIENT_UNITS]
NUTRIENT_UNIT_CHOICES = [("g", "g"), ("mg", "mg"), ("ug", "ug")]


class MealEntryNutrient(models.Model):
    """
    Auxiliary source nutrient value for a MealEntry, beyond its typed core
    fields (calories/protein/carbs/fat/sat_fat/sugar/fiber/salt).

    Absence of a row for a given (meal_entry, nutrient_key) means the value
    is unknown/not supplied - it must never be treated as zero. A row with
    value=0 means the source explicitly reported zero.
    """
    meal_entry = models.ForeignKey(
        MealEntry, on_delete=models.CASCADE, related_name='nutrients'
    )
    nutrient_key = models.CharField(max_length=50, choices=NUTRIENT_KEY_CHOICES)
    value = models.FloatField(help_text="Numeric value in `unit`; 0 is a valid known value")
    unit = models.CharField(max_length=4, choices=NUTRIENT_UNIT_CHOICES)
    qualifier = models.CharField(
        max_length=10, choices=NUTRIENT_QUALIFIER_CHOICES, default="exact",
        help_text="'exact' for a reported number, 'lt' when the source gave a below-detection-limit threshold (e.g. '<0.1')"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['meal_entry', 'nutrient_key'], name='unique_meal_entry_nutrient')
        ]
        verbose_name = "Meal Entry Nutrient"
        verbose_name_plural = "Meal Entry Nutrients"

    def clean(self):
        from django.core.exceptions import ValidationError
        expected_unit = NUTRIENT_UNITS.get(self.nutrient_key)
        if expected_unit and self.unit != expected_unit:
            raise ValidationError(
                f"nutrient_key '{self.nutrient_key}' must use unit '{expected_unit}', got '{self.unit}'"
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.meal_entry_id}:{self.nutrient_key}={self.value}{self.unit}"


class FineliFoodNutrient(models.Model):
    """
    Auxiliary source nutrient value for a FineliFood, beyond its typed core
    fields (energy_kcal/protein_g/carbs_g/fat_g/sat_fat_g/sugar_g/fiber_g/
    salt_g). Same semantics as MealEntryNutrient: absence of a row for a
    given (fineli_food, nutrient_key) means the value is unknown/not
    reported by Fineli for that food - it must never be treated as zero. A
    row with value=0 means Fineli explicitly reported zero.

    The local official Fineli Release 20 catalog (component_value.csv) has
    no below-detection-limit ("<x") representation - every BESTLOC value is
    plain numeric - so qualifier is always "exact" for catalog-imported rows.
    """
    fineli_food = models.ForeignKey(
        FineliFood, on_delete=models.CASCADE, related_name='nutrients'
    )
    nutrient_key = models.CharField(max_length=50, choices=NUTRIENT_KEY_CHOICES)
    value = models.FloatField(help_text="Numeric value in `unit`; 0 is a valid known value")
    unit = models.CharField(max_length=4, choices=NUTRIENT_UNIT_CHOICES)
    qualifier = models.CharField(
        max_length=10, choices=NUTRIENT_QUALIFIER_CHOICES, default="exact",
        help_text="Always 'exact' for catalog imports - the local Fineli dataset has no 'lt' representation"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['fineli_food', 'nutrient_key'], name='unique_finelifood_nutrient')
        ]
        verbose_name = "Fineli Food Nutrient"
        verbose_name_plural = "Fineli Food Nutrients"

    def clean(self):
        from django.core.exceptions import ValidationError
        expected_unit = NUTRIENT_UNITS.get(self.nutrient_key)
        if expected_unit and self.unit != expected_unit:
            raise ValidationError(
                f"nutrient_key '{self.nutrient_key}' must use unit '{expected_unit}', got '{self.unit}'"
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.fineli_food_id}:{self.nutrient_key}={self.value}{self.unit}"


class CustomFoodNutrient(models.Model):
    """
    Auxiliary source nutrient value for a CustomFood, beyond its typed core
    fields (energy_kcal_per_100g/protein_per_100g/carbs_per_100g/
    fat_per_100g/sat_fat_per_100g/sugar_per_100g/fiber_per_100g/
    salt_per_100g). Same semantics as MealEntryNutrient/FineliFoodNutrient:
    absence of a row for a given (custom_food, nutrient_key) means the value
    is unknown/not supplied by the user - it must never be treated as zero.
    A row with value=0 means the user explicitly entered zero.
    """
    custom_food = models.ForeignKey(
        CustomFood, on_delete=models.CASCADE, related_name='nutrients'
    )
    nutrient_key = models.CharField(max_length=50, choices=NUTRIENT_KEY_CHOICES)
    value = models.FloatField(help_text="Numeric value in `unit`; 0 is a valid known value")
    unit = models.CharField(max_length=4, choices=NUTRIENT_UNIT_CHOICES)
    qualifier = models.CharField(
        max_length=10, choices=NUTRIENT_QUALIFIER_CHOICES, default="exact",
        help_text="'exact' for a reported number, 'lt' when the user gave a below-detection-limit threshold (e.g. '<0.1')"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['custom_food', 'nutrient_key'], name='unique_customfood_nutrient')
        ]
        verbose_name = "Custom Food Nutrient"
        verbose_name_plural = "Custom Food Nutrients"

    def clean(self):
        from django.core.exceptions import ValidationError
        expected_unit = NUTRIENT_UNITS.get(self.nutrient_key)
        if expected_unit and self.unit != expected_unit:
            raise ValidationError(
                f"nutrient_key '{self.nutrient_key}' must use unit '{expected_unit}', got '{self.unit}'"
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.custom_food_id}:{self.nutrient_key}={self.value}{self.unit}"


def replace_custom_food_nutrients(custom_food, rows):
    """Full replacement of a CustomFood's auxiliary nutrients - not a merge.
    `rows` is an iterable of {nutrient_key, value, qualifier} dicts; a
    nutrient_key omitted from `rows` stays unknown (no row), matching the
    same no-row-means-unknown semantics as MealEntryNutrient/
    FineliFoodNutrient. `unit` is always derived from the canonical
    NUTRIENT_UNITS map (never trusted from a caller), so it can't drift from
    the key. Does not touch the 8 typed core CustomFood fields. Not wired to
    any view yet - for service-level/test use ahead of a future UI."""
    custom_food.nutrients.all().delete()
    CustomFoodNutrient.objects.bulk_create([
        CustomFoodNutrient(
            custom_food=custom_food,
            nutrient_key=row['nutrient_key'],
            value=row['value'],
            unit=NUTRIENT_UNITS[row['nutrient_key']],
            qualifier=row.get('qualifier', 'exact'),
        )
        for row in rows
    ])


def _custom_food_recipe_would_cycle(parent_id, candidate_source_id) -> bool:
    """True if custom_food(parent_id) using custom_food(candidate_source_id)
    as an ingredient would create a cycle - i.e. candidate_source_id's own
    ingredient graph already (transitively) reaches parent_id. Deterministic
    breadth-first traversal of source_custom_food edges only; no depth
    limit, no recursion - a `seen` set prevents revisiting a node."""
    if parent_id == candidate_source_id:
        return True
    seen = {candidate_source_id}
    frontier = [candidate_source_id]
    while frontier:
        next_ids = CustomFoodIngredient.objects.filter(
            custom_food_id__in=frontier, source_custom_food__isnull=False
        ).values_list('source_custom_food_id', flat=True)
        frontier = []
        for nid in next_ids:
            if nid == parent_id:
                return True
            if nid not in seen:
                seen.add(nid)
                frontier.append(nid)
    return False


class CustomFoodIngredient(models.Model):
    """One ingredient line of a CustomFood recipe. Ownership is inherited
    through custom_food (the parent recipe) - deliberately no separate user
    FK here. Exactly one of fineli_food (shared catalog, any user) /
    source_custom_food (must be owned by the same user as the recipe) is
    set. Gram-based only in this slice - no serving/unit conversion.

    Nested source_custom_food ingredients contribute their OWN currently
    persisted per-100g values in aggregation - their own ingredients are not
    recursively expanded yet (see recipe_aggregation.py). Cycle prevention
    still applies now so a later recursive-recalculation slice stays safe.
    """
    custom_food = models.ForeignKey(
        CustomFood, on_delete=models.CASCADE, related_name='ingredients'
    )
    fineli_food = models.ForeignKey(
        FineliFood, on_delete=models.CASCADE, null=True, blank=True, related_name='+'
    )
    source_custom_food = models.ForeignKey(
        CustomFood, on_delete=models.CASCADE, null=True, blank=True, related_name='+'
    )
    amount_grams = models.FloatField(help_text="Ingredient quantity in grams; must be > 0")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(fineli_food__isnull=False, source_custom_food__isnull=True)
                    | models.Q(fineli_food__isnull=True, source_custom_food__isnull=False)
                ),
                name='ingredient_exactly_one_source',
            ),
            models.CheckConstraint(
                condition=models.Q(amount_grams__gt=0), name='ingredient_amount_grams_positive'
            ),
        ]
        verbose_name = "Custom Food Ingredient"
        verbose_name_plural = "Custom Food Ingredients"

    def clean(self):
        from django.core.exceptions import ValidationError
        if bool(self.fineli_food_id) == bool(self.source_custom_food_id):
            raise ValidationError("Exactly one of fineli_food or source_custom_food must be set.")
        if self.amount_grams is None or self.amount_grams <= 0:
            raise ValidationError("amount_grams must be greater than zero.")
        if self.source_custom_food_id is not None:
            if self.source_custom_food_id == self.custom_food_id:
                raise ValidationError("A recipe cannot contain itself as an ingredient.")
            if (
                self.source_custom_food.user_id is None
                or self.source_custom_food.user_id != self.custom_food.user_id
            ):
                raise ValidationError(
                    "A recipe ingredient's source CustomFood must be owned by the same user as the recipe."
                )
            if _custom_food_recipe_would_cycle(self.custom_food_id, self.source_custom_food_id):
                raise ValidationError("This ingredient would create a circular recipe reference.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
        # A food with ingredients is a recipe.
        if self.custom_food.kind != CustomFood.KIND_RECIPE:
            CustomFood.objects.filter(pk=self.custom_food_id).update(kind=CustomFood.KIND_RECIPE)
            self.custom_food.kind = CustomFood.KIND_RECIPE

    def __str__(self):
        source = self.fineli_food or self.source_custom_food
        return f"{self.amount_grams}g {source} in {self.custom_food}"


class FavoriteFood(models.Model):
    """
    A user's favorited food - either a FineliFood or a CustomFood, never
    both. Favorites are per-user, like MealEntry.user-owned diary entries
    and the Recent/Frequent food discovery derived from them.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='favorite_foods'
    )
    fineli_food = models.ForeignKey(
        FineliFood, on_delete=models.CASCADE, null=True, blank=True, related_name='favorited_by'
    )
    custom_food = models.ForeignKey(
        CustomFood, on_delete=models.CASCADE, null=True, blank=True, related_name='favorited_by'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'fineli_food'], name='unique_user_fineli_favorite',
                condition=models.Q(fineli_food__isnull=False),
            ),
            models.UniqueConstraint(
                fields=['user', 'custom_food'], name='unique_user_custom_favorite',
                condition=models.Q(custom_food__isnull=False),
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(fineli_food__isnull=False, custom_food__isnull=True)
                    | models.Q(fineli_food__isnull=True, custom_food__isnull=False)
                ),
                name='favorite_exactly_one_food_source',
            ),
        ]
        verbose_name = "Favorite Food"
        verbose_name_plural = "Favorite Foods"

    def __str__(self):
        food = self.fineli_food or self.custom_food
        return f"{self.user}: {food}"
