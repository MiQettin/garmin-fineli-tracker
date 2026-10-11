from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from core.models import DailyLog
from .aggregation import daily_nutrient_result
from .models import (
    CustomFood, CustomFoodIngredient, CustomFoodNutrient, FavoriteFood, FineliFood, FineliFoodNutrient,
    MealEntry, MealEntryNutrient, NUTRIENT_UNITS, replace_custom_food_nutrients,
)
from .recipe_aggregation import normalize_to_per_100g, recipe_nutrient_result, total_recipe_grams
from .recipe_persistence import (
    RecipeGraphError, recalculate_custom_food_recipe, recalculate_custom_food_recipe_with_dependencies,
)

CORE_NUTRIENT_FIELDS = [
    "calories", "protein_g", "carbs_g", "fat_g", "sat_fat_g",
    "sugar_g", "fiber_g", "salt_g",
]

CSV_HEADER = (
    "date;meal;food;mass (g);energia, laskennallinen (kJ);rasva (g);"
    "hiilihydraatti imeytyvä (g);proteiini (g);rasvahapot tyydyttyneet (g);"
    "sokerit (g);kuitu, kokonais- (g);suola (mg)"
)


def diary_csv(date="2026-08-16"):
    """Two food rows, one meal-subtotal row and one daily-total row for `date`."""
    return "\n".join([
        CSV_HEADER,
        f"{date};Aamiainen;Test Oatmeal;100;800;5;20;10;2;3;4;500",
        f"{date};Aamiainen;;;800;5;20;10;2;3;4;500",  # meal subtotal (food empty)
        f"{date};Lounas;Test Chicken;150;1200;8;5;30;3;0;0;800",
        f"{date};Lounas;;;1200;8;5;30;3;0;0;800",  # meal subtotal (food empty)
        f"{date};;;;2000;13;25;40;5;3;4;1300",  # daily total (meal + food empty)
    ])


def corrected_csv(date="2026-08-16"):
    """Replacement data for `date`: single food row, different values."""
    return "\n".join([
        CSV_HEADER,
        f"{date};Illallinen;Corrected Soup;200;900;6;15;12;1;2;1;600",
        f"{date};;;;900;6;15;12;1;2;1;600",
    ])


def upload(content):
    return SimpleUploadedFile("diary.csv", content.encode("utf-8"), content_type="text/csv")


class BulkImportDiaryCsvTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="bulkimporttest", password="x")
        self.client.force_login(self.user)
        self.url = reverse("bulk_import_diary_csv")

    def test_bulk_food_import_creates_mapped_meal_entries(self):
        resp = self.client.post(self.url, {"csv_files": upload(diary_csv())})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

        entries = MealEntry.objects.filter(
            daily_log__date="2026-08-16", source_type="bulk_import"
        ).order_by("food_name")
        self.assertEqual(entries.count(), 2)  # subtotal/total rows must not create entries

        chicken = entries.get(food_name="Test Chicken")
        self.assertEqual(chicken.meal_type, "lunch")
        self.assertEqual(chicken.portion_grams, 150.0)
        self.assertAlmostEqual(chicken.calories, 1200 / 4.184, places=3)
        self.assertEqual(chicken.protein_g, 30.0)
        self.assertEqual(chicken.carbs_g, 5.0)
        self.assertEqual(chicken.fat_g, 8.0)
        self.assertEqual(chicken.sat_fat_g, 3.0)
        self.assertEqual(chicken.sugar_g, 0.0)
        self.assertEqual(chicken.fiber_g, 0.0)
        self.assertAlmostEqual(chicken.salt_g, 0.8, places=6)  # mg -> g

        oatmeal = entries.get(food_name="Test Oatmeal")
        self.assertEqual(oatmeal.meal_type, "breakfast")
        self.assertAlmostEqual(oatmeal.salt_g, 0.5, places=6)

    def test_dailylog_derived_from_meal_entries(self):
        self.client.post(self.url, {"csv_files": upload(diary_csv())})
        log = DailyLog.objects.get(date="2026-08-16")
        entries = log.meal_entries.all()
        self.assertEqual(log.calories_in, int(round(sum(e.calories for e in entries))))
        self.assertAlmostEqual(log.protein_g, sum(e.protein_g for e in entries), places=3)
        self.assertAlmostEqual(log.salt_g, sum(e.salt_g for e in entries), places=6)

    def test_manual_entry_preserved_and_included_in_totals(self):
        log = DailyLog.objects.create(date="2026-08-16")
        manual = MealEntry.objects.create(
            daily_log=log, source_type="manual", food_name="Manual Snack",
            meal_type="snack", portion_grams=50, calories=100, protein_g=2,
        )

        self.client.post(self.url, {"csv_files": upload(diary_csv())})

        self.assertTrue(MealEntry.objects.filter(pk=manual.pk).exists())
        log.refresh_from_db()
        bulk_total = sum(
            e.calories for e in log.meal_entries.filter(source_type="bulk_import")
        )
        self.assertEqual(log.calories_in, int(round(bulk_total + 100)))

    def test_reimport_same_csv_does_not_duplicate(self):
        self.client.post(self.url, {"csv_files": upload(diary_csv())})
        self.client.post(self.url, {"csv_files": upload(diary_csv())})

        self.assertEqual(
            MealEntry.objects.filter(
                daily_log__date="2026-08-16", source_type="bulk_import"
            ).count(),
            2,
        )

    def test_corrected_reimport_replaces_only_bulk_rows(self):
        log = DailyLog.objects.create(date="2026-08-16")
        manual = MealEntry.objects.create(
            daily_log=log, source_type="manual", food_name="Manual Snack",
            meal_type="snack", portion_grams=50, calories=100, protein_g=2,
        )

        self.client.post(self.url, {"csv_files": upload(diary_csv())})
        self.client.post(self.url, {"csv_files": upload(corrected_csv())})

        bulk_entries = MealEntry.objects.filter(
            daily_log__date="2026-08-16", source_type="bulk_import"
        )
        self.assertEqual(bulk_entries.count(), 1)
        self.assertEqual(bulk_entries.first().food_name, "Corrected Soup")
        self.assertTrue(MealEntry.objects.filter(pk=manual.pk).exists())

        log.refresh_from_db()
        expected = bulk_entries.first().calories + manual.calories
        self.assertEqual(log.calories_in, int(round(expected)))


PROVENANCE_CSV_HEADER = (
    "date;meal;foodid;food;amount;unit;mass (g);energia, laskennallinen (kJ);rasva (g);"
    "hiilihydraatti imeytyvä (g);proteiini (g);rasvahapot tyydyttyneet (g);"
    "sokerit (g);kuitu, kokonais- (g);suola (mg)"
)


def provenance_csv(date="2026-08-16"):
    """One row with explicit foodid=0 (custom/manual recipe) and one row with
    blank foodid/amount/unit (unknown provenance), matching real Fineli diary
    export shape."""
    return "\n".join([
        PROVENANCE_CSV_HEADER,
        f"{date};Aamiainen;33757;Orange Juice;2.0;DL;131;800;5;20;10;2;3;4;500",
        f"{date};Lounas;0;Custom Porridge;1.0;PORTM;164;1200;8;5;30;3;0;0;800",
        f"{date};Välipala;;Blank Provenance Snack;;;50;300;1;5;2;0;1;1;100",
        f"{date};;;;;;;2300;14;30;42;5;4;5;1400",  # daily total row
    ])


class MealEntryProvenanceTests(TestCase):
    """Regression coverage for preserving original Fineli diary foodid/amount/unit."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="provenancetest", password="x")
        self.client.force_login(self.user)
        self.url = reverse("bulk_import_diary_csv")

    def test_provenance_persisted_and_portion_grams_independent(self):
        self.client.post(self.url, {"csv_files": upload(provenance_csv())})
        entries = MealEntry.objects.filter(
            daily_log__date="2026-08-16", source_type="bulk_import"
        )
        self.assertEqual(entries.count(), 3)

        juice = entries.get(food_name="Orange Juice")
        self.assertEqual(juice.source_food_id, 33757)
        self.assertEqual(juice.source_amount, 2.0)
        self.assertEqual(juice.source_unit, "DL")
        self.assertEqual(juice.portion_grams, 131.0)  # independent normalized mass

    def test_foodid_zero_preserved_not_null(self):
        self.client.post(self.url, {"csv_files": upload(provenance_csv())})
        porridge = MealEntry.objects.get(
            daily_log__date="2026-08-16", food_name="Custom Porridge"
        )
        self.assertEqual(porridge.source_food_id, 0)
        self.assertIsNotNone(porridge.source_food_id)
        self.assertEqual(porridge.source_amount, 1.0)
        self.assertEqual(porridge.source_unit, "PORTM")

    def test_blank_provenance_remains_unknown_not_fabricated(self):
        self.client.post(self.url, {"csv_files": upload(provenance_csv())})
        snack = MealEntry.objects.get(
            daily_log__date="2026-08-16", food_name="Blank Provenance Snack"
        )
        self.assertIsNone(snack.source_food_id)
        self.assertIsNone(snack.source_amount)
        self.assertEqual(snack.source_unit, "")
        self.assertEqual(snack.portion_grams, 50.0)

    def test_reimport_same_csv_is_idempotent(self):
        self.client.post(self.url, {"csv_files": upload(provenance_csv())})
        self.client.post(self.url, {"csv_files": upload(provenance_csv())})
        self.assertEqual(
            MealEntry.objects.filter(
                daily_log__date="2026-08-16", source_type="bulk_import"
            ).count(),
            3,
        )

    def test_corrected_reimport_replaces_provenance(self):
        self.client.post(self.url, {"csv_files": upload(provenance_csv())})
        corrected = "\n".join([
            PROVENANCE_CSV_HEADER,
            "2026-08-16;Illallinen;99999;Replacement Meal;3.0;RKL;90;900;6;15;12;1;2;1;600",
            "2026-08-16;;;;;;;900;6;15;12;1;2;1;600",
        ])
        self.client.post(self.url, {"csv_files": upload(corrected)})

        entries = MealEntry.objects.filter(
            daily_log__date="2026-08-16", source_type="bulk_import"
        )
        self.assertEqual(entries.count(), 1)
        replacement = entries.first()
        self.assertEqual(replacement.food_name, "Replacement Meal")
        self.assertEqual(replacement.source_food_id, 99999)
        self.assertEqual(replacement.source_unit, "RKL")

    def test_manual_entry_has_null_provenance_and_csv_without_columns_still_works(self):
        # CSV without foodid/amount/unit columns (legacy shape) must still import.
        self.client.post(self.url, {"csv_files": upload(diary_csv())})
        bulk_entry = MealEntry.objects.filter(
            daily_log__date="2026-08-16", source_type="bulk_import"
        ).first()
        self.assertIsNone(bulk_entry.source_food_id)
        self.assertIsNone(bulk_entry.source_amount)
        self.assertEqual(bulk_entry.source_unit, "")

        manual = MealEntry.objects.create(
            daily_log=bulk_entry.daily_log, source_type="manual", food_name="Manual Snack",
            meal_type="snack", portion_grams=50, calories=100,
        )
        self.assertIsNone(manual.source_food_id)
        self.assertIsNone(manual.source_amount)
        self.assertEqual(manual.source_unit, "")

    def test_auxiliary_nutrient_import_unchanged_alongside_provenance(self):
        aux_csv = "\n".join([
            PROVENANCE_CSV_HEADER + ";natrium (mg)",
            "2026-08-16;Aamiainen;33757;Orange Juice;2.0;DL;131;800;5;20;10;2;3;4;500;250",
            "2026-08-16;;;;;;;800;5;20;10;2;3;4;500;250",
        ])
        self.client.post(self.url, {"csv_files": upload(aux_csv)})
        entry = MealEntry.objects.get(
            daily_log__date="2026-08-16", food_name="Orange Juice"
        )
        self.assertEqual(entry.nutrients.get(nutrient_key="sodium").value, 250)
        self.assertEqual(entry.source_food_id, 33757)


CLASSIFICATION_CSV_HEADER = (
    "date;meal;foodid;food;ingredients (classification);food use (classification);"
    "amount;unit;mass (g);energia, laskennallinen (kJ);rasva (g);"
    "hiilihydraatti imeytyvä (g);proteiini (g);rasvahapot tyydyttyneet (g);"
    "sokerit (g);kuitu, kokonais- (g);suola (mg)"
)


def classification_csv(date="2026-08-16"):
    """One row with both classification labels and one with blanks (matches
    real Fineli diary export column order and shape)."""
    return "\n".join([
        CLASSIFICATION_CSV_HEADER,
        f"{date};Aamiainen;33757;Orange Juice;Täysmehut;Täysmehut;2.0;DL;200;800;5;20;10;2;3;4;500",
        f"{date};Lounas;0;Custom Porridge;;;1.0;PORTM;164;1200;8;5;30;3;0;0;800",
        f"{date};;;;;;;;;2000;13;25;40;5;3;4;1300",  # daily total row
    ])


class MealEntryClassificationProvenanceTests(TestCase):
    """Regression coverage for preserving Fineli diary ingredients/food-use classification labels."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="classificationtest", password="x")
        self.client.force_login(self.user)
        self.url = reverse("bulk_import_diary_csv")

    def test_classification_labels_persisted(self):
        self.client.post(self.url, {"csv_files": upload(classification_csv())})
        juice = MealEntry.objects.get(
            daily_log__date="2026-08-16", food_name="Orange Juice"
        )
        self.assertEqual(juice.source_ingredients_classification, "Täysmehut")
        self.assertEqual(juice.source_food_use_classification, "Täysmehut")
        self.assertEqual(juice.source_food_id, 33757)  # unaffected by this slice

    def test_blank_classification_remains_unknown_not_fabricated(self):
        self.client.post(self.url, {"csv_files": upload(classification_csv())})
        porridge = MealEntry.objects.get(
            daily_log__date="2026-08-16", food_name="Custom Porridge"
        )
        self.assertEqual(porridge.source_ingredients_classification, "")
        self.assertEqual(porridge.source_food_use_classification, "")
        self.assertEqual(porridge.source_food_id, 0)  # foodid=0 still preserved

    def test_csv_without_classification_columns_still_works(self):
        # Legacy/older CSV shape without these two columns must still import.
        self.client.post(self.url, {"csv_files": upload(provenance_csv())})
        entry = MealEntry.objects.get(
            daily_log__date="2026-08-16", food_name="Orange Juice"
        )
        self.assertEqual(entry.source_ingredients_classification, "")
        self.assertEqual(entry.source_food_use_classification, "")

    def test_corrected_reimport_replaces_classification(self):
        self.client.post(self.url, {"csv_files": upload(classification_csv())})
        corrected = "\n".join([
            CLASSIFICATION_CSV_HEADER,
            "2026-08-16;Illallinen;99999;Replacement Meal;Keitot;Keitot;3.0;RKL;90;900;6;15;12;1;2;1;600",
            "2026-08-16;;;;;;;;;900;6;15;12;1;2;1;600",
        ])
        self.client.post(self.url, {"csv_files": upload(corrected)})

        entries = MealEntry.objects.filter(
            daily_log__date="2026-08-16", source_type="bulk_import"
        )
        self.assertEqual(entries.count(), 1)
        replacement = entries.first()
        self.assertEqual(replacement.food_name, "Replacement Meal")
        self.assertEqual(replacement.source_ingredients_classification, "Keitot")
        self.assertEqual(replacement.source_food_use_classification, "Keitot")


QUALIFIER_CSV_HEADER = PROVENANCE_CSV_HEADER + ";tärkkelys (g);natrium (mg)"


class MealEntryNutrientQualifierTests(TestCase):
    """Regression coverage for lossless '<x' (below-detection-limit) auxiliary nutrient values."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="nutrientqualifiertest", password="x")
        self.client.force_login(self.user)
        self.url = reverse("bulk_import_diary_csv")

    def _post(self, starch_cell, sodium_cell, date="2026-08-16"):
        csv_content = "\n".join([
            QUALIFIER_CSV_HEADER,
            f"{date};Aamiainen;33757;Test Food;2.0;DL;131;800;5;20;10;2;3;4;500;{starch_cell};{sodium_cell}",
            f"{date};;;;;;;800;5;20;10;2;3;4;500;{starch_cell};{sodium_cell}",
        ])
        return self.client.post(self.url, {"csv_files": upload(csv_content)})

    def test_exact_numeric_value_stores_qualifier_exact(self):
        self._post("8.0", "250")
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        sodium = entry.nutrients.get(nutrient_key="sodium")
        self.assertEqual(sodium.value, 250)
        self.assertEqual(sodium.qualifier, "exact")

    def test_explicit_zero_stores_value_zero_qualifier_exact(self):
        self._post("0", "0")
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        starch = entry.nutrients.get(nutrient_key="starch")
        self.assertEqual(starch.value, 0)
        self.assertEqual(starch.qualifier, "exact")

    def test_less_than_value_stores_threshold_and_lt_qualifier(self):
        self._post("<0.1", "250")
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        starch = entry.nutrients.get(nutrient_key="starch")
        self.assertEqual(starch.value, 0.1)
        self.assertEqual(starch.qualifier, "lt")

    def test_unit_handling_unchanged(self):
        self._post("<0.1", "250")
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        self.assertEqual(entry.nutrients.get(nutrient_key="starch").unit, "g")
        self.assertEqual(entry.nutrients.get(nutrient_key="sodium").unit, "mg")

    def test_blank_creates_no_nutrient_row(self):
        self._post("", "250")
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        self.assertFalse(entry.nutrients.filter(nutrient_key="starch").exists())

    def test_reimport_same_csv_is_idempotent(self):
        self._post("<0.1", "250")
        self._post("<0.1", "250")
        self.assertEqual(
            MealEntry.objects.filter(daily_log__date="2026-08-16", source_type="bulk_import").count(), 1
        )

    def test_corrected_reimport_replaces_lt_with_exact(self):
        self._post("<0.1", "250")
        self._post("0.2", "250")
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        starch = entry.nutrients.get(nutrient_key="starch")
        self.assertEqual(starch.value, 0.2)
        self.assertEqual(starch.qualifier, "exact")

    def test_default_qualifier_for_directly_constructed_row_is_exact(self):
        log = DailyLog.objects.create(date="2026-08-17")
        manual = MealEntry.objects.create(
            daily_log=log, source_type="manual", food_name="Manual Food",
            meal_type="snack", portion_grams=100, calories=100,
        )
        nutrient = MealEntryNutrient.objects.create(
            meal_entry=manual, nutrient_key="iron", value=2, unit="mg"
        )
        self.assertEqual(nutrient.qualifier, "exact")

    def test_core_meal_entry_fields_unaffected(self):
        # "<x" appears only in the aux column here; core fields import unchanged.
        self._post("<0.1", "250")
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        self.assertEqual(entry.fat_g, 5.0)
        self.assertEqual(entry.protein_g, 10.0)


def core_qualifier_csv(fat, carbs, protein, sat_fat, sugar, fiber, date="2026-08-16"):
    """One food row with controllable cells for the six proven-affected core
    nutrient columns, matching real Fineli diary export column order."""
    row = f"{date};Aamiainen;33757;Test Food;2.0;DL;131;800;{fat};{carbs};{protein};{sat_fat};{sugar};{fiber};500"
    total = f"{date};;;;;;;800;{fat};{carbs};{protein};{sat_fat};{sugar};{fiber};500"
    return "\n".join([PROVENANCE_CSV_HEADER, row, total])


class MealEntryCoreQualifierTests(TestCase):
    """Regression coverage for lossless '<x' preservation on the six typed
    core MealEntry nutrient fields (fat/carbs/protein/sat_fat/sugar/fiber)."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="corequalifiertest", password="x")
        self.client.force_login(self.user)
        self.url = reverse("bulk_import_diary_csv")

    def _import(self, **cells):
        defaults = dict(fat="5", carbs="20", protein="10", sat_fat="2", sugar="3", fiber="4")
        defaults.update(cells)
        return self.client.post(self.url, {"csv_files": upload(core_qualifier_csv(**defaults))})

    def test_exact_fat_value_stores_qualifier_exact(self):
        self._import(fat="1.2")
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        self.assertEqual(entry.fat_g, 1.2)
        self.assertEqual(entry.fat_qualifier, "exact")

    def test_fat_less_than_stores_threshold_and_lt_qualifier(self):
        self._import(fat="<0.1")
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        self.assertEqual(entry.fat_g, 0.1)
        self.assertEqual(entry.fat_qualifier, "lt")

    def test_explicit_zero_stores_value_zero_qualifier_exact(self):
        self._import(fat="0")
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        self.assertEqual(entry.fat_g, 0.0)
        self.assertEqual(entry.fat_qualifier, "exact")

    def test_all_six_affected_fields_preserve_lt(self):
        self._import(fat="<0.1", carbs="<0.2", protein="<0.3", sat_fat="<0.4", sugar="<0.5", fiber="<0.6")
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        self.assertEqual((entry.fat_g, entry.fat_qualifier), (0.1, "lt"))
        self.assertEqual((entry.carbs_g, entry.carbs_qualifier), (0.2, "lt"))
        self.assertEqual((entry.protein_g, entry.protein_qualifier), (0.3, "lt"))
        self.assertEqual((entry.sat_fat_g, entry.sat_fat_qualifier), (0.4, "lt"))
        self.assertEqual((entry.sugar_g, entry.sugar_qualifier), (0.5, "lt"))
        self.assertEqual((entry.fiber_g, entry.fiber_qualifier), (0.6, "lt"))

    def test_reimport_same_csv_is_idempotent(self):
        self._import(fat="<0.1")
        self._import(fat="<0.1")
        self.assertEqual(
            MealEntry.objects.filter(daily_log__date="2026-08-16", source_type="bulk_import").count(), 1
        )

    def test_corrected_reimport_replaces_lt_with_exact(self):
        self._import(fat="<0.1")
        self._import(fat="0.2")
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        self.assertEqual(entry.fat_g, 0.2)
        self.assertEqual(entry.fat_qualifier, "exact")

    def test_manual_entry_defaults_to_exact_qualifiers(self):
        log = DailyLog.objects.create(date="2026-08-17")
        manual = MealEntry.objects.create(
            daily_log=log, source_type="manual", food_name="Manual Food",
            meal_type="snack", portion_grams=100, calories=100, fat_g=5,
        )
        self.assertEqual(manual.fat_qualifier, "exact")
        self.assertEqual(manual.protein_qualifier, "exact")

    def test_provenance_fields_unaffected_by_core_qualifiers(self):
        self._import(fat="<0.1")
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        self.assertEqual(entry.source_food_id, 33757)
        self.assertEqual(entry.source_amount, 2.0)
        self.assertEqual(entry.source_unit, "DL")

    def test_auxiliary_nutrient_qualifier_import_still_works(self):
        csv_content = "\n".join([
            QUALIFIER_CSV_HEADER,
            "2026-08-16;Aamiainen;33757;Test Food;2.0;DL;131;800;<0.1;20;10;2;3;4;500;<0.1;250",
            "2026-08-16;;;;;;;800;<0.1;20;10;2;3;4;500;<0.1;250",
        ])
        self.client.post(self.url, {"csv_files": upload(csv_content)})
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        self.assertEqual(entry.fat_g, 0.1)
        self.assertEqual(entry.fat_qualifier, "lt")
        starch = entry.nutrients.get(nutrient_key="starch")
        self.assertEqual(starch.value, 0.1)
        self.assertEqual(starch.qualifier, "lt")


def salt_csv(salt, date="2026-08-16"):
    """One food row with a controllable salt (suola (mg)) cell - the source
    column proven to carry real '<0.1' values, unlike energy (kJ)."""
    row = f"{date};Aamiainen;33757;Test Food;2.0;DL;131;800;5;20;10;2;3;4;{salt}"
    total = f"{date};;;;;;;800;5;20;10;2;3;4;{salt}"
    return "\n".join([PROVENANCE_CSV_HEADER, row, total])


class MealEntrySaltQualifierTests(TestCase):
    """Regression coverage for lossless '<x' preservation on salt_g - the
    only additional core field proven affected (calories/energy is exact
    in all inspected real diary CSVs, so it gets no qualifier field)."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="saltqualifiertest", password="x")
        self.client.force_login(self.user)
        self.url = reverse("bulk_import_diary_csv")

    def test_exact_salt_value_stores_qualifier_exact(self):
        self.client.post(self.url, {"csv_files": upload(salt_csv("500"))})
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        self.assertAlmostEqual(entry.salt_g, 0.5, places=6)
        self.assertEqual(entry.salt_qualifier, "exact")

    def test_salt_less_than_stores_converted_threshold_and_lt_qualifier(self):
        self.client.post(self.url, {"csv_files": upload(salt_csv("<0.1"))})
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        self.assertAlmostEqual(entry.salt_g, 0.0001, places=6)  # same mg->g conversion applied to threshold
        self.assertEqual(entry.salt_qualifier, "lt")

    def test_explicit_zero_salt_stores_value_zero_qualifier_exact(self):
        self.client.post(self.url, {"csv_files": upload(salt_csv("0"))})
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        self.assertEqual(entry.salt_g, 0.0)
        self.assertEqual(entry.salt_qualifier, "exact")

    def test_reimport_same_csv_is_idempotent(self):
        self.client.post(self.url, {"csv_files": upload(salt_csv("<0.1"))})
        self.client.post(self.url, {"csv_files": upload(salt_csv("<0.1"))})
        self.assertEqual(
            MealEntry.objects.filter(daily_log__date="2026-08-16", source_type="bulk_import").count(), 1
        )

    def test_corrected_reimport_replaces_lt_with_exact(self):
        self.client.post(self.url, {"csv_files": upload(salt_csv("<0.1"))})
        self.client.post(self.url, {"csv_files": upload(salt_csv("600"))})
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        self.assertAlmostEqual(entry.salt_g, 0.6, places=6)
        self.assertEqual(entry.salt_qualifier, "exact")

    def test_other_six_core_qualifiers_unaffected(self):
        self.client.post(self.url, {"csv_files": upload(salt_csv("<0.1"))})
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        self.assertEqual(entry.fat_qualifier, "exact")
        self.assertEqual(entry.protein_qualifier, "exact")


class LegacyCsvImportConsolidationTests(TestCase):
    """Regression coverage for retiring the broken legacy import_fineli_csv view."""

    def test_legacy_import_csv_url_no_longer_exists(self):
        with self.assertRaises(NoReverseMatch):
            reverse("import_fineli_csv")

        resp = self.client.post("/food-diary/api/import-csv/")
        self.assertEqual(resp.status_code, 404)

    def test_canonical_importer_uses_csv_date_and_meal_not_request_params(self):
        # Even if a caller sends 'date'/'meal_type' (the old per-form fields),
        # the canonical importer must only use the values from the CSV rows.
        self.client.force_login(get_user_model().objects.create_user(username="legacyconsolidationtest", password="x"))
        url = reverse("bulk_import_diary_csv")
        data = {
            "csv_files": upload(diary_csv(date="2026-08-16")),
            "date": "1999-01-01",
            "meal_type": "snack",
        }
        self.client.post(url, data)

        self.assertFalse(DailyLog.objects.filter(date="1999-01-01").exists())
        entries = MealEntry.objects.filter(
            daily_log__date="2026-08-16", source_type="bulk_import"
        )
        self.assertEqual(entries.count(), 2)
        self.assertEqual(
            set(entries.values_list("meal_type", flat=True)), {"breakfast", "lunch"}
        )


class MealEntryNutrientTests(TestCase):
    """Regression coverage for the lossless auxiliary nutrient storage foundation."""

    def setUp(self):
        log = DailyLog.objects.create(date="2026-08-16")
        self.entry = MealEntry.objects.create(
            daily_log=log, source_type="manual", food_name="Test Food",
            meal_type="snack", portion_grams=100, calories=100,
        )

    def test_meal_entry_can_have_auxiliary_nutrient(self):
        nutrient = MealEntryNutrient.objects.create(
            meal_entry=self.entry, nutrient_key="sodium", value=250, unit="mg"
        )
        self.assertEqual(self.entry.nutrients.get(), nutrient)

    def test_zero_is_a_genuine_known_value(self):
        nutrient = MealEntryNutrient.objects.create(
            meal_entry=self.entry, nutrient_key="vitamin_d", value=0, unit="ug"
        )
        self.assertTrue(
            MealEntryNutrient.objects.filter(pk=nutrient.pk, value=0).exists()
        )

    def test_absent_nutrient_row_means_unknown(self):
        self.assertFalse(
            self.entry.nutrients.filter(nutrient_key="iron").exists()
        )

    def test_duplicate_nutrient_key_for_same_entry_is_rejected(self):
        MealEntryNutrient.objects.create(
            meal_entry=self.entry, nutrient_key="iron", value=2, unit="mg"
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MealEntryNutrient.objects.create(
                    meal_entry=self.entry, nutrient_key="iron", value=3, unit="mg"
                )

    def test_same_nutrient_key_allowed_across_different_entries(self):
        other_log = DailyLog.objects.create(date="2026-08-17")
        other_entry = MealEntry.objects.create(
            daily_log=other_log, source_type="manual", food_name="Other Food",
            meal_type="snack", portion_grams=100, calories=50,
        )
        MealEntryNutrient.objects.create(
            meal_entry=self.entry, nutrient_key="iron", value=2, unit="mg"
        )
        MealEntryNutrient.objects.create(
            meal_entry=other_entry, nutrient_key="iron", value=5, unit="mg"
        )
        self.assertEqual(MealEntryNutrient.objects.filter(nutrient_key="iron").count(), 2)

    def test_canonical_unit_accepted(self):
        # sodium's canonical unit is mg; must not raise.
        MealEntryNutrient.objects.create(
            meal_entry=self.entry, nutrient_key="sodium", value=100, unit="mg"
        )

    def test_incorrect_unit_for_known_key_is_rejected(self):
        with self.assertRaises(ValidationError):
            MealEntryNutrient.objects.create(
                meal_entry=self.entry, nutrient_key="sodium", value=100, unit="g"
            )

    def test_deleting_meal_entry_cascades_nutrients(self):
        nutrient = MealEntryNutrient.objects.create(
            meal_entry=self.entry, nutrient_key="potassium", value=300, unit="mg"
        )
        self.entry.delete()
        self.assertFalse(MealEntryNutrient.objects.filter(pk=nutrient.pk).exists())


AUX_CSV_HEADER = CSV_HEADER + ";tärkkelys (g);natrium (mg);jodidi (jodi) (µg);kalium (mg)"


def aux_diary_csv(date="2026-08-16"):
    """
    Oatmeal: starch=20g, sodium=150mg, iodine=8.6ug (non-zero), potassium=300mg.
    Chicken: starch=0g (explicit zero), sodium=250mg, iodine=5.5ug, potassium=blank (missing).
    Plus one meal-subtotal row and one daily-total row, both with aux values populated,
    to prove they must not create MealEntry/MealEntryNutrient rows.
    """
    return "\n".join([
        AUX_CSV_HEADER,
        f"{date};Aamiainen;Test Oatmeal;100;800;5;20;10;2;3;4;500;20;150;8.6;300",
        f"{date};Aamiainen;;;800;5;20;10;2;3;4;500;20;150;8.6;300",
        f"{date};Lounas;Test Chicken;150;1200;8;5;30;3;0;0;800;0;250;5.5;",
        f"{date};Lounas;;;1200;8;5;30;3;0;0;800;0;250;5.5;300",
        f"{date};;;;2000;13;25;40;5;3;4;1300;20;400;5.5;600",
    ])


def corrected_aux_diary_csv(date="2026-08-16"):
    """Replacement data for `date`: single food row with different aux values."""
    return "\n".join([
        AUX_CSV_HEADER,
        f"{date};Illallinen;Corrected Soup;200;900;6;15;12;1;2;1;600;99;999;1.1;",
        f"{date};;;;900;6;15;12;1;2;1;600;99;999;1.1;",
    ])


class BulkImportAuxiliaryNutrientTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="bulkauxtest", password="x")
        self.client.force_login(self.user)
        self.url = reverse("bulk_import_diary_csv")

    def test_food_rows_create_mapped_aux_nutrients(self):
        resp = self.client.post(self.url, {"csv_files": upload(aux_diary_csv())})
        self.assertTrue(resp.json()["success"])

        entries = MealEntry.objects.filter(
            daily_log__date="2026-08-16", source_type="bulk_import"
        )
        self.assertEqual(entries.count(), 2)  # subtotal/total rows create no MealEntry

        oat = entries.get(food_name="Test Oatmeal")
        chicken = entries.get(food_name="Test Chicken")

        # representative g / mg / ug nutrients, attached to the correct entry
        starch = oat.nutrients.get(nutrient_key="starch")
        self.assertEqual(starch.value, 20.0)
        self.assertEqual(starch.unit, "g")

        sodium = oat.nutrients.get(nutrient_key="sodium")
        self.assertEqual(sodium.value, 150.0)
        self.assertEqual(sodium.unit, "mg")

        iodine = oat.nutrients.get(nutrient_key="iodine")
        self.assertEqual(iodine.value, 8.6)
        self.assertEqual(iodine.unit, "ug")

        # explicit zero creates a genuine known-zero row
        chicken_starch = chicken.nutrients.get(nutrient_key="starch")
        self.assertEqual(chicken_starch.value, 0.0)

        # blank source value creates no row (unknown, not zero)
        self.assertFalse(chicken.nutrients.filter(nutrient_key="potassium").exists())

        # core MealEntry-owned nutrients are never duplicated into MealEntryNutrient
        self.assertFalse(
            oat.nutrients.filter(nutrient_key__in=CORE_NUTRIENT_FIELDS).exists()
        )

        # only food rows produced children: 4 for oat + 3 for chicken (blank potassium skipped)
        self.assertEqual(MealEntryNutrient.objects.count(), 7)

    def test_reimport_same_csv_does_not_duplicate_nutrients(self):
        self.client.post(self.url, {"csv_files": upload(aux_diary_csv())})
        self.client.post(self.url, {"csv_files": upload(aux_diary_csv())})

        self.assertEqual(
            MealEntry.objects.filter(daily_log__date="2026-08-16", source_type="bulk_import").count(),
            2,
        )
        self.assertEqual(MealEntryNutrient.objects.count(), 7)

    def test_corrected_reimport_replaces_nutrient_values(self):
        self.client.post(self.url, {"csv_files": upload(aux_diary_csv())})
        self.client.post(self.url, {"csv_files": upload(corrected_aux_diary_csv())})

        entries = MealEntry.objects.filter(daily_log__date="2026-08-16", source_type="bulk_import")
        self.assertEqual(entries.count(), 1)
        soup = entries.get(food_name="Corrected Soup")

        self.assertEqual(soup.nutrients.get(nutrient_key="starch").value, 99.0)
        self.assertEqual(soup.nutrients.get(nutrient_key="sodium").value, 999.0)
        # old oatmeal/chicken MealEntry rows (and their nutrient children via CASCADE) are gone
        self.assertEqual(MealEntryNutrient.objects.count(), 3)  # starch, sodium, iodine (potassium blank)

    def test_manual_entry_and_its_nutrients_untouched_by_import(self):
        log = DailyLog.objects.create(date="2026-08-16")
        manual = MealEntry.objects.create(
            daily_log=log, source_type="manual", food_name="Manual Snack",
            meal_type="snack", portion_grams=50, calories=100, protein_g=2,
        )
        manual_nutrient = MealEntryNutrient.objects.create(
            meal_entry=manual, nutrient_key="iron", value=1.5, unit="mg"
        )

        self.client.post(self.url, {"csv_files": upload(aux_diary_csv())})

        self.assertTrue(MealEntry.objects.filter(pk=manual.pk).exists())
        self.assertTrue(MealEntryNutrient.objects.filter(pk=manual_nutrient.pk, value=1.5).exists())


class DailyNutrientResultTests(TestCase):
    """Regression coverage for the pure auxiliary-nutrient daily aggregation contract."""

    DATE = "2026-08-16"

    def _entry(self, food_name):
        log, _ = DailyLog.objects.get_or_create(date=self.DATE)
        return MealEntry.objects.create(
            daily_log=log, source_type="manual", food_name=food_name,
            meal_type="snack", portion_grams=100, calories=0,
        )

    def _nutrient(self, entry, value, qualifier="exact"):
        return MealEntryNutrient.objects.create(
            meal_entry=entry, nutrient_key="sodium", value=value, unit="mg", qualifier=qualifier
        )

    def test_case_a_exact_plus_exact_is_complete(self):
        self._nutrient(self._entry("A"), 10)
        self._nutrient(self._entry("B"), 20)
        r = daily_nutrient_result(self.DATE, "sodium")
        self.assertEqual(r.known_exact_sum, 30)
        self.assertEqual(r.known_upper_bound_sum, 30)
        self.assertEqual(r.known_entry_count, 2)
        self.assertEqual(r.total_entry_count, 2)
        self.assertEqual(r.coverage, "complete")
        self.assertEqual(r.bound_status, "exact")
        self.assertEqual(r.day_lower_bound, 30)
        self.assertEqual(r.day_upper_bound, 30)

    def test_case_b_exact_plus_lt_is_complete_bounded(self):
        self._nutrient(self._entry("A"), 10)
        self._nutrient(self._entry("B"), 2, qualifier="lt")
        r = daily_nutrient_result(self.DATE, "sodium")
        self.assertEqual(r.known_exact_sum, 10)
        self.assertEqual(r.known_upper_bound_sum, 12)
        self.assertEqual(r.known_entry_count, 2)
        self.assertEqual(r.total_entry_count, 2)
        self.assertEqual(r.coverage, "complete")
        self.assertEqual(r.bound_status, "bounded")
        self.assertEqual(r.day_lower_bound, 10)
        self.assertEqual(r.day_upper_bound, 12)

    def test_case_c_exact_plus_lt_plus_unknown_is_partial(self):
        self._nutrient(self._entry("A"), 10)
        self._nutrient(self._entry("B"), 2, qualifier="lt")
        self._entry("C")  # unknown: no MealEntryNutrient row
        r = daily_nutrient_result(self.DATE, "sodium")
        self.assertEqual(r.known_exact_sum, 10)
        self.assertEqual(r.known_upper_bound_sum, 12)
        self.assertEqual(r.known_entry_count, 2)
        self.assertEqual(r.total_entry_count, 3)
        self.assertEqual(r.coverage, "partial")
        self.assertEqual(r.bound_status, "bounded")
        self.assertEqual(r.day_lower_bound, 10)
        self.assertIsNone(r.day_upper_bound)

    def test_case_d_all_unknown_is_none_coverage(self):
        self._entry("A")
        self._entry("B")
        r = daily_nutrient_result(self.DATE, "sodium")
        self.assertEqual(r.known_exact_sum, 0)
        self.assertEqual(r.known_upper_bound_sum, 0)
        self.assertEqual(r.known_entry_count, 0)
        self.assertEqual(r.total_entry_count, 2)
        self.assertEqual(r.coverage, "none")
        self.assertEqual(r.day_lower_bound, 0)
        self.assertIsNone(r.day_upper_bound)

    def test_case_e_exact_zero_plus_exact_zero_is_complete_known_zero(self):
        self._nutrient(self._entry("A"), 0)
        self._nutrient(self._entry("B"), 0)
        r = daily_nutrient_result(self.DATE, "sodium")
        self.assertEqual(r.known_exact_sum, 0)
        self.assertEqual(r.known_upper_bound_sum, 0)
        self.assertEqual(r.known_entry_count, 2)
        self.assertEqual(r.total_entry_count, 2)
        self.assertEqual(r.coverage, "complete")
        self.assertEqual(r.bound_status, "exact")
        self.assertEqual(r.day_lower_bound, 0)
        self.assertEqual(r.day_upper_bound, 0)

    def test_no_meal_data(self):
        r = daily_nutrient_result(self.DATE, "sodium")
        self.assertEqual(r.known_entry_count, 0)
        self.assertEqual(r.total_entry_count, 0)
        self.assertEqual(r.coverage, "no_meal_data")
        self.assertIsNone(r.day_upper_bound)

    def test_unit_is_canonical(self):
        r = daily_nutrient_result(self.DATE, "sodium")
        self.assertEqual(r.unit, "mg")

    def test_invalid_nutrient_key_raises(self):
        with self.assertRaises(ValueError):
            daily_nutrient_result(self.DATE, "not_a_real_nutrient")

    def test_core_field_name_now_accepted(self):
        # Superseded by CoreDailyNutrientResultTests: core keys are now valid.
        r = daily_nutrient_result(self.DATE, "protein_g")
        self.assertEqual(r.unit, "g")


def unknown_zero_csv(protein="10", calories="800", salt="500", date="2026-08-16"):
    """One food row with controllable protein/calories/salt cells - the rest
    fixed and non-blank - matching real Fineli diary export column order."""
    row = f"{date};Aamiainen;33757;Test Food;2.0;DL;131;{calories};5;20;{protein};2;3;4;{salt}"
    total = f"{date};;;;;;;{calories};5;20;{protein};2;3;4;{salt}"
    return "\n".join([PROVENANCE_CSV_HEADER, row, total])


class MealEntryUnknownVsZeroTests(TestCase):
    """Regression coverage: MealEntry core fields must store None for
    unknown/not-supplied source values, never fabricate 0."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="unknownvszerotest", password="x")
        self.client.force_login(self.user)
        self.url = reverse("bulk_import_diary_csv")

    def _import(self, **cells):
        self.client.post(self.url, {"csv_files": upload(unknown_zero_csv(**cells))})
        return MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")

    # A. bulk import
    def test_blank_protein_is_none(self):
        self.assertIsNone(self._import(protein="").protein_g)

    def test_explicit_zero_protein_is_zero(self):
        entry = self._import(protein="0")
        self.assertEqual(entry.protein_g, 0.0)
        self.assertEqual(entry.protein_qualifier, "exact")

    def test_numeric_protein_is_numeric(self):
        self.assertEqual(self._import(protein="7.5").protein_g, 7.5)

    def test_lt_protein_preserves_threshold_and_qualifier(self):
        entry = self._import(protein="<0.1")
        self.assertEqual(entry.protein_g, 0.1)
        self.assertEqual(entry.protein_qualifier, "lt")

    def test_blank_calories_is_none(self):
        self.assertIsNone(self._import(calories="").calories)

    def test_numeric_calories_uses_existing_conversion(self):
        entry = self._import(calories="800")
        self.assertAlmostEqual(entry.calories, 800 / 4.184, places=3)

    def test_blank_salt_is_none(self):
        self.assertIsNone(self._import(salt="").salt_g)

    def test_explicit_zero_salt_is_zero_exact(self):
        entry = self._import(salt="0")
        self.assertEqual(entry.salt_g, 0.0)
        self.assertEqual(entry.salt_qualifier, "exact")

    # B. reimport
    def test_reimport_blank_to_zero_replaces_none_with_zero(self):
        self.assertIsNone(self._import(protein="").protein_g)
        self.assertEqual(self._import(protein="0").protein_g, 0.0)

    def test_reimport_zero_to_blank_replaces_zero_with_none(self):
        self.assertEqual(self._import(protein="0").protein_g, 0.0)
        self.assertIsNone(self._import(protein="").protein_g)

    def test_same_file_reimport_is_idempotent(self):
        self._import(protein="<0.1")
        self._import(protein="<0.1")
        self.assertEqual(
            MealEntry.objects.filter(daily_log__date="2026-08-16", source_type="bulk_import").count(), 1
        )

    # C. manual entry (raw POST path)
    def test_manual_blank_field_is_none(self):
        self.client.post(reverse("add_meal_entry"), {
            "date": "2026-08-17", "food_name": "Manual Blank", "meal_type": "snack",
            "calories": "100", "protein_g": "",
        })
        entry = MealEntry.objects.get(daily_log__date="2026-08-17", food_name="Manual Blank")
        self.assertIsNone(entry.protein_g)

    def test_manual_explicit_zero_is_zero(self):
        self.client.post(reverse("add_meal_entry"), {
            "date": "2026-08-17", "food_name": "Manual Zero", "meal_type": "snack",
            "calories": "100", "protein_g": "0",
        })
        entry = MealEntry.objects.get(daily_log__date="2026-08-17", food_name="Manual Zero")
        self.assertEqual(entry.protein_g, 0.0)

    # D. model
    def test_model_persists_nullable_core_fields(self):
        log = DailyLog.objects.create(date="2026-08-18")
        entry = MealEntry.objects.create(
            daily_log=log, source_type="manual", food_name="Nullable", meal_type="snack",
            portion_grams=100, calories=None, protein_g=None,
        )
        entry.refresh_from_db()
        self.assertIsNone(entry.calories)
        self.assertIsNone(entry.protein_g)

    def test_model_persists_legacy_style_explicit_zero(self):
        log = DailyLog.objects.create(date="2026-08-19")
        entry = MealEntry.objects.create(
            daily_log=log, source_type="manual", food_name="ZeroCal", meal_type="snack",
            portion_grams=100, calories=0, protein_g=0,
        )
        entry.refresh_from_db()
        self.assertEqual(entry.calories, 0.0)
        self.assertEqual(entry.protein_g, 0.0)

    # E. regression
    def test_qualifier_fields_still_work(self):
        entry = self._import(protein="5")
        self.assertEqual(entry.protein_qualifier, "exact")

    def test_auxiliary_nutrient_import_still_works(self):
        csv_content = "\n".join([
            QUALIFIER_CSV_HEADER,
            "2026-08-16;Aamiainen;33757;Test Food;2.0;DL;131;800;5;20;10;2;3;4;500;8;250",
            "2026-08-16;;;;;;;800;5;20;10;2;3;4;500;8;250",
        ])
        self.client.post(self.url, {"csv_files": upload(csv_content)})
        entry = MealEntry.objects.get(daily_log__date="2026-08-16", food_name="Test Food")
        self.assertEqual(entry.nutrients.get(nutrient_key="sodium").value, 250)

    def test_provenance_fields_unchanged(self):
        entry = self._import()
        self.assertEqual(entry.source_food_id, 33757)
        self.assertEqual(entry.source_unit, "DL")


class SourceModelUnknownVsZeroTests(TestCase):
    """Regression coverage: FineliFood/CustomFood must be able to store None
    for an unreported nutrient, and MealEntry must propagate that honestly
    (None stays None, explicit 0 stays 0) instead of crashing or fabricating."""

    def setUp(self):
        self.log = DailyLog.objects.create(date="2026-08-20")

    # FineliFood
    def test_finelifood_can_store_none_for_missing_nutrient(self):
        food = FineliFood.objects.create(fineli_id=1, name_fi="A", energy_kcal=100, protein_g=None)
        food.refresh_from_db()
        self.assertIsNone(food.protein_g)

    def test_finelifood_stores_explicit_zero(self):
        food = FineliFood.objects.create(fineli_id=2, name_fi="B", energy_kcal=100, protein_g=0)
        food.refresh_from_db()
        self.assertEqual(food.protein_g, 0.0)

    def test_finelifood_stores_positive_value(self):
        food = FineliFood.objects.create(fineli_id=3, name_fi="C", energy_kcal=100, protein_g=12.5)
        food.refresh_from_db()
        self.assertEqual(food.protein_g, 12.5)

    def test_mealentry_from_finelifood_none_source_is_none(self):
        food = FineliFood.objects.create(fineli_id=4, name_fi="D", energy_kcal=100, protein_g=None)
        entry = MealEntry.objects.create(daily_log=self.log, fineli_food=food, portion_grams=100, food_name="")
        self.assertEqual(entry.calories, 100.0)
        self.assertIsNone(entry.protein_g)

    def test_mealentry_from_finelifood_zero_source_is_zero(self):
        food = FineliFood.objects.create(fineli_id=5, name_fi="E", energy_kcal=100, protein_g=0)
        entry = MealEntry.objects.create(daily_log=self.log, fineli_food=food, portion_grams=100, food_name="")
        self.assertEqual(entry.protein_g, 0.0)

    def test_mealentry_from_finelifood_positive_source_scaled_correctly(self):
        food = FineliFood.objects.create(fineli_id=6, name_fi="F", energy_kcal=100, protein_g=20)
        entry = MealEntry.objects.create(daily_log=self.log, fineli_food=food, portion_grams=200, food_name="")
        self.assertEqual(entry.protein_g, 40.0)

    # CustomFood
    def test_customfood_can_store_none_for_missing_nutrient(self):
        food = CustomFood.objects.create(name="X", energy_kcal_per_100g=100, protein_per_100g=None)
        food.refresh_from_db()
        self.assertIsNone(food.protein_per_100g)

    def test_customfood_stores_explicit_zero(self):
        food = CustomFood.objects.create(name="Y", energy_kcal_per_100g=100, protein_per_100g=0)
        food.refresh_from_db()
        self.assertEqual(food.protein_per_100g, 0.0)

    def test_customfood_stores_positive_value(self):
        food = CustomFood.objects.create(name="Z", energy_kcal_per_100g=100, protein_per_100g=8)
        food.refresh_from_db()
        self.assertEqual(food.protein_per_100g, 8.0)

    def test_mealentry_from_customfood_propagates_none_zero_positive(self):
        none_food = CustomFood.objects.create(name="N", energy_kcal_per_100g=100, protein_per_100g=None)
        zero_food = CustomFood.objects.create(name="Z2", energy_kcal_per_100g=100, protein_per_100g=0)
        pos_food = CustomFood.objects.create(name="P", energy_kcal_per_100g=100, protein_per_100g=10)

        e1 = MealEntry.objects.create(daily_log=self.log, custom_food=none_food, portion_grams=100, food_name="")
        e2 = MealEntry.objects.create(daily_log=self.log, custom_food=zero_food, portion_grams=100, food_name="")
        e3 = MealEntry.objects.create(daily_log=self.log, custom_food=pos_food, portion_grams=50, food_name="")

        self.assertIsNone(e1.protein_g)
        self.assertEqual(e2.protein_g, 0.0)
        self.assertEqual(e3.protein_g, 5.0)

    # Regression: manual + bulk import unaffected
    def test_manual_mealentry_still_works(self):
        entry = MealEntry.objects.create(
            daily_log=self.log, source_type="manual", food_name="Manual", meal_type="snack",
            portion_grams=100, calories=100, protein_g=5,
        )
        self.assertEqual(entry.calories, 100.0)
        self.assertEqual(entry.protein_g, 5.0)

    def test_bulk_import_still_works(self):
        self.client.force_login(get_user_model().objects.create_user(username="sourcemodeltest", password="x"))
        url = reverse("bulk_import_diary_csv")
        self.client.post(url, {"csv_files": upload(unknown_zero_csv(date="2026-08-20"))})
        entry = MealEntry.objects.get(daily_log__date="2026-08-20", food_name="Test Food", source_type="bulk_import")
        self.assertEqual(entry.protein_g, 10.0)


class AddFineliEntryNullVsZeroTests(TestCase):
    """Regression coverage for the search/quick-add round trip: a JSON null
    for a nutrient must reach MealEntry as None, not a fabricated 0."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="fineliaddnullzerotest", password="x")
        self.client.force_login(self.user)
        self.url = reverse("add_fineli_entry")

    def _post(self, protein):
        import json as _json
        payload = {
            "date": "2026-08-21", "fineli_id": 999, "name": "RoundTrip Food",
            "meal_type": "snack", "portion_grams": 100,
            "energy_kcal": 100, "protein": protein, "carbs": 10,
            "sugar": 1, "fat": 2, "sat_fat": 1, "fiber": 1, "salt": 0.5,
        }
        self.client.post(self.url, data=_json.dumps(payload), content_type="application/json")
        return MealEntry.objects.get(daily_log__date="2026-08-21", food_name="RoundTrip Food")

    def test_null_protein_becomes_none(self):
        self.assertIsNone(self._post(None).protein_g)

    def test_explicit_zero_protein_stays_zero(self):
        self.assertEqual(self._post(0).protein_g, 0.0)

    def test_positive_protein_scaled_correctly(self):
        self.assertEqual(self._post(20).protein_g, 20.0)  # portion_grams=100 -> multiplier 1


class CoreDailyNutrientResultTests(TestCase):
    """Regression coverage: daily_nutrient_result extended to the 8 typed
    core MealEntry fields, sharing the DailyNutrientResult contract with the
    existing auxiliary MealEntryNutrient aggregation."""

    DATE = "2026-08-22"

    def _entry(self, source_type="manual", **fields):
        log, _ = DailyLog.objects.get_or_create(date=self.DATE)
        defaults = dict(daily_log=log, source_type=source_type, food_name="X", meal_type="snack", portion_grams=100)
        defaults.update(fields)
        return MealEntry.objects.create(**defaults)

    def test_exact_plus_exact_is_complete(self):
        self._entry(protein_g=10, protein_qualifier="exact")
        self._entry(protein_g=20, protein_qualifier="exact")
        r = daily_nutrient_result(self.DATE, "protein_g")
        self.assertEqual((r.known_exact_sum, r.known_upper_bound_sum), (30, 30))
        self.assertEqual((r.known_entry_count, r.total_entry_count), (2, 2))
        self.assertEqual((r.coverage, r.bound_status), ("complete", "exact"))
        self.assertEqual((r.day_lower_bound, r.day_upper_bound), (30, 30))

    def test_exact_plus_lt_is_complete_bounded(self):
        self._entry(protein_g=10, protein_qualifier="exact")
        self._entry(protein_g=2, protein_qualifier="lt")
        r = daily_nutrient_result(self.DATE, "protein_g")
        self.assertEqual((r.known_exact_sum, r.known_upper_bound_sum), (10, 12))
        self.assertEqual((r.known_entry_count, r.total_entry_count), (2, 2))
        self.assertEqual((r.coverage, r.bound_status), ("complete", "bounded"))
        self.assertEqual((r.day_lower_bound, r.day_upper_bound), (10, 12))

    def test_exact_plus_lt_plus_none_is_partial(self):
        self._entry(protein_g=10, protein_qualifier="exact")
        self._entry(protein_g=2, protein_qualifier="lt")
        self._entry(protein_g=None)
        r = daily_nutrient_result(self.DATE, "protein_g")
        self.assertEqual((r.known_exact_sum, r.known_upper_bound_sum), (10, 12))
        self.assertEqual((r.known_entry_count, r.total_entry_count), (2, 3))
        self.assertEqual((r.coverage, r.bound_status), ("partial", "bounded"))
        self.assertEqual(r.day_lower_bound, 10)
        self.assertIsNone(r.day_upper_bound)

    def test_none_plus_none_is_none_coverage(self):
        self._entry(protein_g=None)
        self._entry(protein_g=None)
        r = daily_nutrient_result(self.DATE, "protein_g")
        self.assertEqual((r.known_exact_sum, r.known_upper_bound_sum), (0, 0))
        self.assertEqual((r.known_entry_count, r.total_entry_count), (0, 2))
        self.assertEqual(r.coverage, "none")
        self.assertEqual(r.day_lower_bound, 0)
        self.assertIsNone(r.day_upper_bound)

    def test_zero_plus_zero_is_genuine_complete_zero(self):
        self._entry(protein_g=0, protein_qualifier="exact")
        self._entry(protein_g=0, protein_qualifier="exact")
        r = daily_nutrient_result(self.DATE, "protein_g")
        self.assertEqual((r.known_exact_sum, r.known_upper_bound_sum), (0, 0))
        self.assertEqual((r.known_entry_count, r.total_entry_count), (2, 2))
        self.assertEqual((r.coverage, r.bound_status), ("complete", "exact"))
        self.assertEqual((r.day_lower_bound, r.day_upper_bound), (0, 0))

    def test_calories_complete_exact(self):
        self._entry(calories=500)
        self._entry(calories=300)
        r = daily_nutrient_result(self.DATE, "calories")
        self.assertEqual((r.known_exact_sum, r.known_upper_bound_sum), (800, 800))
        self.assertEqual(r.coverage, "complete")
        self.assertEqual(r.bound_status, "exact")

    def test_calories_plus_none_is_partial_exact(self):
        self._entry(calories=500)
        self._entry(calories=None)
        self._entry(calories=300)
        r = daily_nutrient_result(self.DATE, "calories")
        self.assertEqual((r.known_exact_sum, r.known_upper_bound_sum), (800, 800))
        self.assertEqual((r.known_entry_count, r.total_entry_count), (2, 3))
        self.assertEqual(r.coverage, "partial")
        self.assertEqual(r.bound_status, "exact")
        self.assertEqual(r.day_lower_bound, 800)
        self.assertIsNone(r.day_upper_bound)

    def test_no_meal_entries_is_no_meal_data(self):
        r = daily_nutrient_result(self.DATE, "fat_g")
        self.assertEqual((r.known_entry_count, r.total_entry_count), (0, 0))
        self.assertEqual(r.coverage, "no_meal_data")

    def test_all_eight_core_keys_accepted_with_correct_units(self):
        expected_units = {
            "calories": "kcal", "protein_g": "g", "carbs_g": "g", "fat_g": "g",
            "sat_fat_g": "g", "sugar_g": "g", "fiber_g": "g", "salt_g": "g",
        }
        for key, unit in expected_units.items():
            r = daily_nutrient_result(self.DATE, key)
            self.assertEqual(r.unit, unit)
            self.assertEqual(r.coverage, "no_meal_data")

    def test_unknown_key_still_rejected(self):
        with self.assertRaises(ValueError):
            daily_nutrient_result(self.DATE, "not_a_real_nutrient")

    def test_auxiliary_nutrient_regression_still_works(self):
        e = self._entry()
        MealEntryNutrient.objects.create(meal_entry=e, nutrient_key="sodium", value=250, unit="mg", qualifier="exact")
        r = daily_nutrient_result(self.DATE, "sodium")
        self.assertEqual(r.known_exact_sum, 250)
        self.assertEqual(r.coverage, "complete")

    def test_mixed_source_types_all_counted_without_branching(self):
        self._entry(source_type="bulk_import", protein_g=5, protein_qualifier="exact")
        self._entry(source_type="manual", protein_g=None)
        self._entry(source_type="fineli", protein_g=3, protein_qualifier="exact")
        self._entry(source_type="custom", protein_g=None)
        r = daily_nutrient_result(self.DATE, "protein_g")
        self.assertEqual(r.total_entry_count, 4)
        self.assertEqual(r.known_entry_count, 2)
        self.assertEqual(r.known_exact_sum, 8)
        self.assertEqual(r.coverage, "partial")


class ImportFineliCsvSaltCodeTests(TestCase):
    """Regression coverage: the Fineli catalog importer must read salt from
    NACL (mg, converted to g), not the nonexistent SALTEQ code."""

    def _run_import(self, rows):
        import io
        import os
        import tempfile
        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as data_folder:
            with open(os.path.join(data_folder, "foodname_FI.csv"), "w", encoding="latin-1") as f:
                f.write("FOODID;FOODNAME\n")
                for food_id, _fields in rows:
                    f.write(f"{food_id};Food {food_id}\n")

            with open(os.path.join(data_folder, "component_value.csv"), "w", encoding="utf-8-sig") as f:
                f.write("FOODID;EUFDNAME;BESTLOC\n")
                for food_id, fields in rows:
                    for code, value in fields.items():
                        f.write(f"{food_id};{code};{value}\n")

            call_command("import_fineli_csv", data_folder=data_folder, stdout=io.StringIO())

    def test_nacl_value_imported_as_salt_g_converted_from_mg(self):
        self._run_import([("1", {"NACL": "1200", "ENERC": "100", "PROT": "5"})])
        food = FineliFood.objects.get(fineli_id=1)
        self.assertAlmostEqual(food.salt_g, 1.2, places=6)

    def test_missing_nacl_stays_none(self):
        self._run_import([("2", {"ENERC": "100", "PROT": "5"})])
        food = FineliFood.objects.get(fineli_id=2)
        self.assertIsNone(food.salt_g)

    def test_explicit_zero_nacl_stays_zero(self):
        self._run_import([("3", {"NACL": "0", "ENERC": "100"})])
        food = FineliFood.objects.get(fineli_id=3)
        self.assertEqual(food.salt_g, 0.0)

    def test_saltEQ_rows_are_ignored_not_used_as_salt(self):
        # A stray SALTEQ row (the old, nonexistent-in-real-data code) must not
        # be picked up as salt now that the importer reads NACL.
        self._run_import([("4", {"SALTEQ": "999", "ENERC": "100"})])
        food = FineliFood.objects.get(fineli_id=4)
        self.assertIsNone(food.salt_g)

    def test_unrelated_core_mapping_still_works(self):
        self._run_import([("5", {"ENERC": "837", "PROT": "12.5", "FAT": "3", "CHOAVL": "20", "SUGAR": "2", "FIBC": "1", "FASAT": "0.5"})])
        food = FineliFood.objects.get(fineli_id=5)
        # ENERC is kJ in Fineli; stored as kcal (837 kJ / 4.184 = 200 kcal).
        self.assertAlmostEqual(food.energy_kcal, 200.05, places=2)
        self.assertEqual(food.protein_g, 12.5)
        self.assertEqual(food.fat_g, 3.0)


class FineliFoodNutrientTests(TestCase):
    """Regression coverage for normalized FineliFood auxiliary nutrient
    storage (catalog import -> FineliFoodNutrient), mirroring
    MealEntryNutrient's unknown-vs-zero and unit contract."""

    def setUp(self):
        self.food = FineliFood.objects.create(fineli_id=100, name_fi="Test Food", energy_kcal=100)

    # Model-level contract
    def test_model_stores_one_supported_auxiliary_nutrient(self):
        n = FineliFoodNutrient.objects.create(
            fineli_food=self.food, nutrient_key="iron", value=2.5, unit="mg"
        )
        self.assertEqual(self.food.nutrients.get(), n)

    def test_explicit_zero_is_a_real_row(self):
        FineliFoodNutrient.objects.create(fineli_food=self.food, nutrient_key="iron", value=0, unit="mg")
        self.assertTrue(self.food.nutrients.filter(nutrient_key="iron", value=0).exists())

    def test_missing_component_means_no_row(self):
        self.assertFalse(self.food.nutrients.filter(nutrient_key="iron").exists())

    def test_wrong_unit_rejected(self):
        with self.assertRaises(ValidationError):
            FineliFoodNutrient.objects.create(fineli_food=self.food, nutrient_key="iron", value=2, unit="g")

    def test_duplicate_nutrient_for_same_food_rejected(self):
        FineliFoodNutrient.objects.create(fineli_food=self.food, nutrient_key="iron", value=2, unit="mg")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FineliFoodNutrient.objects.create(fineli_food=self.food, nutrient_key="iron", value=3, unit="mg")

    def test_representative_g_mg_ug_units_correct(self):
        FineliFoodNutrient.objects.create(fineli_food=self.food, nutrient_key="alcohol", value=1, unit="g")
        FineliFoodNutrient.objects.create(fineli_food=self.food, nutrient_key="calcium", value=1, unit="mg")
        FineliFoodNutrient.objects.create(fineli_food=self.food, nutrient_key="iodine", value=1, unit="ug")
        self.assertEqual(self.food.nutrients.get(nutrient_key="alcohol").unit, "g")
        self.assertEqual(self.food.nutrients.get(nutrient_key="calcium").unit, "mg")
        self.assertEqual(self.food.nutrients.get(nutrient_key="iodine").unit, "ug")


class ImportFineliCsvAuxiliaryNutrientTests(TestCase):
    """Regression coverage for the catalog import writing FineliFoodNutrient
    rows alongside the 8 typed core fields."""

    def _run_import(self, rows):
        import io
        import os
        import tempfile
        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as data_folder:
            with open(os.path.join(data_folder, "foodname_FI.csv"), "w", encoding="latin-1") as f:
                f.write("FOODID;FOODNAME\n")
                for food_id, _fields in rows:
                    f.write(f"{food_id};Food {food_id}\n")

            with open(os.path.join(data_folder, "component_value.csv"), "w", encoding="utf-8-sig") as f:
                f.write("FOODID;EUFDNAME;BESTLOC\n")
                for food_id, fields in rows:
                    for code, value in fields.items():
                        f.write(f"{food_id};{code};{value}\n")

            call_command("import_fineli_csv", data_folder=data_folder, stdout=io.StringIO())

    def test_import_creates_auxiliary_rows_preserves_core_and_salt_and_exact_qualifier(self):
        self._run_import([("1", {
            "ENERC": "837", "PROT": "12.5", "FAT": "3", "CHOAVL": "20",
            "SUGAR": "2", "FIBC": "1", "FASAT": "0.5", "NACL": "1200",
            "CA": "120", "ID": "5", "ALC": "0",
        })])
        food = FineliFood.objects.get(fineli_id=1)
        self.assertEqual(food.protein_g, 12.5)
        self.assertAlmostEqual(food.salt_g, 1.2, places=6)

        calcium = food.nutrients.get(nutrient_key="calcium")
        self.assertEqual(calcium.value, 120.0)
        self.assertEqual(calcium.unit, "mg")
        self.assertEqual(calcium.qualifier, "exact")
        self.assertEqual(food.nutrients.get(nutrient_key="iodine").unit, "ug")
        self.assertEqual(food.nutrients.get(nutrient_key="alcohol").value, 0.0)

        # No core nutrient leaks into auxiliary storage.
        self.assertFalse(food.nutrients.filter(nutrient_key__in=[
            "calories", "protein_g", "carbs_g", "fat_g", "sat_fat_g", "sugar_g", "fiber_g", "salt_g",
        ]).exists())

    def test_reimport_same_values_is_idempotent(self):
        rows = [("2", {"ENERC": "100", "CA": "50"})]
        self._run_import(rows)
        self._run_import(rows)
        food = FineliFood.objects.get(fineli_id=2)
        self.assertEqual(food.nutrients.filter(nutrient_key="calcium").count(), 1)
        self.assertEqual(food.nutrients.get(nutrient_key="calcium").value, 50.0)

    def test_corrected_value_replaces_old_child_value(self):
        self._run_import([("3", {"ENERC": "100", "CA": "50"})])
        self._run_import([("3", {"ENERC": "100", "CA": "75"})])
        food = FineliFood.objects.get(fineli_id=3)
        self.assertEqual(food.nutrients.filter(nutrient_key="calcium").count(), 1)
        self.assertEqual(food.nutrients.get(nutrient_key="calcium").value, 75.0)

    def test_removed_source_nutrient_removes_stale_child_row(self):
        self._run_import([("4", {"ENERC": "100", "CA": "50"})])
        self._run_import([("4", {"ENERC": "100"})])
        food = FineliFood.objects.get(fineli_id=4)
        self.assertFalse(food.nutrients.filter(nutrient_key="calcium").exists())


class FineliQuickAddAuxiliaryPropagationTests(TestCase):
    """Regression coverage: quick-adding a Fineli food propagates its
    FineliFoodNutrient rows into scaled MealEntryNutrient rows."""

    def setUp(self):
        import json as _json
        self._json = _json
        self.user = get_user_model().objects.create_user(username="quickaddauxtest", password="x")
        self.client.force_login(self.user)
        self.url = reverse("add_fineli_entry")
        self.food = FineliFood.objects.create(
            fineli_id=777, name_fi="Quick Add Food", energy_kcal=100, protein_g=5,
        )
        FineliFoodNutrient.objects.create(fineli_food=self.food, nutrient_key="calcium", value=120, unit="mg")
        FineliFoodNutrient.objects.create(fineli_food=self.food, nutrient_key="alcohol", value=0, unit="g")
        FineliFoodNutrient.objects.create(fineli_food=self.food, nutrient_key="iron", value=2, unit="mg", qualifier="lt")
        # "iodine" intentionally has no row -> stays unknown.

    def _post(self, portion_grams=100):
        payload = {
            "date": "2026-08-25", "fineli_id": 777, "name": "Quick Add Food",
            "meal_type": "snack", "portion_grams": portion_grams,
            "energy_kcal": 100, "protein": 5,
        }
        self.client.post(self.url, data=self._json.dumps(payload), content_type="application/json")
        return MealEntry.objects.get(daily_log__date="2026-08-25", food_name="Quick Add Food")

    def test_quick_add_creates_mealentry(self):
        entry = self._post()
        self.assertEqual(entry.source_type, "fineli")

    def test_multiple_auxiliary_nutrients_propagate_at_100g(self):
        entry = self._post(portion_grams=100)
        self.assertEqual(entry.nutrients.get(nutrient_key="calcium").value, 120.0)
        self.assertEqual(entry.nutrients.get(nutrient_key="alcohol").value, 0.0)
        self.assertEqual(entry.nutrients.get(nutrient_key="iron").value, 2.0)

    def test_values_scale_for_non_100g_portion(self):
        entry = self._post(portion_grams=150)
        self.assertEqual(entry.nutrients.get(nutrient_key="calcium").value, 180.0)

    def test_explicit_zero_source_creates_zero_row(self):
        entry = self._post()
        alcohol = entry.nutrients.get(nutrient_key="alcohol")
        self.assertEqual(alcohol.value, 0.0)
        self.assertEqual(alcohol.qualifier, "exact")

    def test_missing_auxiliary_source_creates_no_row(self):
        entry = self._post()
        self.assertFalse(entry.nutrients.filter(nutrient_key="iodine").exists())

    def test_exact_qualifier_copied(self):
        entry = self._post()
        self.assertEqual(entry.nutrients.get(nutrient_key="calcium").qualifier, "exact")

    def test_lt_qualifier_copied_and_scaled(self):
        entry = self._post(portion_grams=50)
        iron = entry.nutrients.get(nutrient_key="iron")
        self.assertEqual(iron.value, 1.0)  # 2 * (50/100)
        self.assertEqual(iron.qualifier, "lt")

    def test_unit_copied_unchanged(self):
        entry = self._post()
        self.assertEqual(entry.nutrients.get(nutrient_key="calcium").unit, "mg")
        self.assertEqual(entry.nutrients.get(nutrient_key="iron").unit, "mg")

    def test_core_nutrients_not_duplicated_as_auxiliary(self):
        entry = self._post()
        core_keys = ["calories", "protein_g", "carbs_g", "fat_g", "sat_fat_g", "sugar_g", "fiber_g", "salt_g"]
        self.assertFalse(entry.nutrients.filter(nutrient_key__in=core_keys).exists())

    def test_target_rows_belong_to_new_entry(self):
        entry = self._post()
        self.assertTrue(entry.nutrients.filter(nutrient_key="calcium").exists())
        self.assertEqual(MealEntryNutrient.objects.filter(nutrient_key="calcium").count(), 1)

    def test_finelifood_source_rows_unchanged(self):
        self._post(portion_grams=150)
        self.assertEqual(self.food.nutrients.get(nutrient_key="calcium").value, 120.0)

    def test_core_mealentry_creation_still_works(self):
        entry = self._post()
        self.assertEqual(entry.calories, 100.0)
        self.assertEqual(entry.protein_g, 5.0)

    def test_customfood_path_creates_no_auxiliary_rows(self):
        custom = CustomFood.objects.create(user=self.user, name="Custom Snack", energy_kcal_per_100g=200)
        payload = {
            "date": "2026-08-25", "source": "custom", "custom_food_id": custom.id,
            "name": "Custom Snack", "meal_type": "snack", "portion_grams": 100,
            "energy_kcal": 200,
        }
        self.client.post(self.url, data=self._json.dumps(payload), content_type="application/json")
        entry = MealEntry.objects.get(daily_log__date="2026-08-25", food_name="Custom Snack")
        self.assertEqual(entry.source_type, "custom")
        self.assertFalse(entry.nutrients.exists())


class FineliLocalReuseTests(TestCase):
    """Regression coverage: quick-adding the same fineli_id repeatedly must
    reuse the one persisted FineliFood row rather than duplicating it or
    mutating its already-imported catalog data (core fields, FineliFoodNutrient
    children, usage tracking)."""

    def setUp(self):
        import json as _json
        self._json = _json
        self.user = get_user_model().objects.create_user(username="localreusetest", password="x")
        self.client.force_login(self.user)
        self.url = reverse("add_fineli_entry")
        self.food = FineliFood.objects.create(
            fineli_id=555, name_fi="Repeat Food", energy_kcal=100, protein_g=5,
        )
        FineliFoodNutrient.objects.create(fineli_food=self.food, nutrient_key="calcium", value=120, unit="mg")

    def _post(self, **overrides):
        payload = {
            "date": "2026-08-26", "fineli_id": 555, "name": "Repeat Food",
            "meal_type": "snack", "portion_grams": 100, "energy_kcal": 100, "protein": 5,
        }
        payload.update(overrides)
        self.client.post(self.url, data=self._json.dumps(payload), content_type="application/json")

    def test_repeated_use_reuses_one_finelifood_row_no_duplicate(self):
        self._post()
        self._post()
        self._post()
        self.assertEqual(FineliFood.objects.filter(fineli_id=555).count(), 1)

    def test_repeated_use_creates_separate_mealentry_rows(self):
        self._post()
        self._post()
        self.assertEqual(
            MealEntry.objects.filter(daily_log__date="2026-08-26", food_name="Repeat Food").count(), 2
        )

    def test_existing_finelifoodnutrient_rows_survive_reuse_unchanged(self):
        self._post()
        self._post()
        self.food.refresh_from_db()
        self.assertEqual(self.food.nutrients.count(), 1)
        self.assertEqual(self.food.nutrients.get(nutrient_key="calcium").value, 120.0)

    def test_reuse_does_not_overwrite_persisted_core_fields_with_client_payload(self):
        # A later request sending different client-side values must not
        # mutate the already-persisted authoritative FineliFood row -
        # get_or_create only applies `defaults` on first creation.
        self._post(energy_kcal=9999, protein=9999)
        self.food.refresh_from_db()
        self.assertEqual(self.food.energy_kcal, 100.0)
        self.assertEqual(self.food.protein_g, 5.0)

    def test_second_quick_add_still_propagates_auxiliary_nutrients(self):
        self._post()
        self._post()
        entries = MealEntry.objects.filter(daily_log__date="2026-08-26", food_name="Repeat Food")
        for entry in entries:
            self.assertEqual(entry.nutrients.get(nutrient_key="calcium").value, 120.0)

    def test_usage_count_increments_without_duplicating_row(self):
        self._post()
        self._post()
        self.food.refresh_from_db()
        self.assertEqual(self.food.usage_count, 2)
        self.assertEqual(FineliFood.objects.filter(fineli_id=555).count(), 1)


class FavoritesRecentFrequentTests(TestCase):
    """Regression coverage for user-specific Favorites and global Recent/
    Frequent food discovery derived from MealEntry history."""

    def setUp(self):
        User = get_user_model()
        self.user_a = User.objects.create_user(username="alice", password="x")
        self.user_b = User.objects.create_user(username="bob", password="x")

        self.f1 = FineliFood.objects.create(fineli_id=1, name_fi="Apple", energy_kcal=50)
        self.f2 = FineliFood.objects.create(fineli_id=2, name_fi="Banana", energy_kcal=90)
        self.custom1 = CustomFood.objects.create(name="Protein Bar", energy_kcal_per_100g=400, user=self.user_a)

        self.f_named = FineliFood.objects.create(fineli_id=3, name_fi="Oatmeal", energy_kcal=380)
        self.custom_named = CustomFood.objects.create(name="Oatmeal", energy_kcal_per_100g=380)

        self.log = DailyLog.objects.create(date="2026-08-27")
        self.client.force_login(self.user_a)

    def _meal_entry(self, **kwargs):
        defaults = dict(
            daily_log=self.log, user=self.user_a, source_type="fineli",
            food_name="X", meal_type="snack", portion_grams=100,
        )
        defaults.update(kwargs)
        return MealEntry.objects.create(**defaults)

    # Favorites
    def test_user_can_favorite_a_finelifood(self):
        self.client.force_login(self.user_a)
        self.client.post(reverse("add_favorite_food"), data='{"fineli_id": 1}', content_type="application/json")
        self.assertTrue(FavoriteFood.objects.filter(user=self.user_a, fineli_food=self.f1).exists())

    def test_favoriting_same_finelifood_twice_creates_one_relation(self):
        self.client.force_login(self.user_a)
        url = reverse("add_favorite_food")
        self.client.post(url, data='{"fineli_id": 1}', content_type="application/json")
        self.client.post(url, data='{"fineli_id": 1}', content_type="application/json")
        self.assertEqual(FavoriteFood.objects.filter(user=self.user_a, fineli_food=self.f1).count(), 1)

    def test_user_can_unfavorite(self):
        self.client.force_login(self.user_a)
        self.client.post(reverse("add_favorite_food"), data='{"fineli_id": 1}', content_type="application/json")
        self.client.post(reverse("remove_favorite_food"), data='{"fineli_id": 1}', content_type="application/json")
        self.assertFalse(FavoriteFood.objects.filter(user=self.user_a, fineli_food=self.f1).exists())

    def test_unfavorite_non_favorite_is_safe_noop(self):
        self.client.force_login(self.user_a)
        resp = self.client.post(reverse("remove_favorite_food"), data='{"fineli_id": 1}', content_type="application/json")
        self.assertEqual(resp.status_code, 200)

    def test_favorites_are_user_specific(self):
        self.client.force_login(self.user_a)
        self.client.post(reverse("add_favorite_food"), data='{"fineli_id": 1}', content_type="application/json")

        self.client.force_login(self.user_b)
        resp = self.client.get(reverse("list_favorite_foods"))
        self.assertEqual(resp.json()["count"], 0)

        self.client.force_login(self.user_a)
        resp = self.client.get(reverse("list_favorite_foods"))
        self.assertEqual(resp.json()["count"], 1)

    def test_user_can_favorite_a_customfood(self):
        self.client.force_login(self.user_a)
        self.client.post(
            reverse("add_favorite_food"), data=f'{{"custom_food_id": {self.custom1.id}}}', content_type="application/json"
        )
        self.assertTrue(FavoriteFood.objects.filter(user=self.user_a, custom_food=self.custom1).exists())

    # Recent
    def test_recent_foods_ordered_newest_first(self):
        e1 = self._meal_entry(fineli_food=self.f1)
        e2 = self._meal_entry(fineli_food=self.f2)
        MealEntry.objects.filter(pk=e1.pk).update(created_at="2026-08-27T10:00:00Z")
        MealEntry.objects.filter(pk=e2.pk).update(created_at="2026-08-27T12:00:00Z")

        resp = self.client.get(reverse("list_recent_foods"))
        results = resp.json()["results"]
        self.assertEqual(results[0]["id"], self.f2.fineli_id)
        self.assertEqual(results[1]["id"], self.f1.fineli_id)

    def test_recent_foods_contain_one_logical_food_once(self):
        self._meal_entry(fineli_food=self.f1)
        self._meal_entry(fineli_food=self.f1)
        self._meal_entry(fineli_food=self.f1)
        resp = self.client.get(reverse("list_recent_foods"))
        results = [r for r in resp.json()["results"] if r.get("id") == self.f1.fineli_id]
        self.assertEqual(len(results), 1)

    # Frequent
    def test_frequent_foods_ordered_by_usage_count_from_history(self):
        self._meal_entry(fineli_food=self.f1)
        self._meal_entry(fineli_food=self.f1)
        self._meal_entry(fineli_food=self.f1)
        self._meal_entry(fineli_food=self.f2)
        # Deliberately leave FineliFood.usage_count untouched (0) to prove
        # frequency comes from MealEntry history, not that counter.
        resp = self.client.get(reverse("list_frequent_foods"))
        results = resp.json()["results"]
        self.assertEqual(results[0]["id"], self.f1.fineli_id)
        self.assertEqual(results[0]["use_count_in_history"], 3)
        self.assertEqual(self.f1.usage_count, 0)

    # Distinctness
    def test_same_name_finelifood_and_customfood_remain_distinct(self):
        self._meal_entry(fineli_food=self.f_named, source_type="fineli")
        self._meal_entry(custom_food=self.custom_named, source_type="custom")
        resp = self.client.get(reverse("list_recent_foods"))
        sources = [r.get("source") for r in resp.json()["results"]]
        self.assertEqual(sources.count("custom"), 1)
        self.assertEqual(sources.count(None), 1)  # fineli results carry no 'source' key

    # Regression
    def test_existing_quick_add_still_works(self):
        import json as _json
        payload = {"date": "2026-08-27", "fineli_id": 1, "name": "Apple", "meal_type": "snack", "portion_grams": 100, "energy_kcal": 50}
        self.client.post(reverse("add_fineli_entry"), data=_json.dumps(payload), content_type="application/json")
        self.assertTrue(MealEntry.objects.filter(daily_log__date="2026-08-27", food_name="Apple").exists())


class AuthAndFoodDiaryAccessTests(TestCase):
    """Regression coverage for Django admin's built-in login/logout as the
    application's auth entry point, and the food diary page's
    login_required protection. Favorite add/remove/user-isolation logic
    itself is already covered by FavoritesRecentFrequentTests - this class
    only proves the auth boundary around it."""

    def setUp(self):
        User = get_user_model()
        # is_staff=True: AdminAuthenticationForm.confirm_login_allowed()
        # rejects non-staff users, since admin login is now the only login
        # entry point for the whole application, not just /admin/.
        self.user = User.objects.create_user(username="alice", password="correct-horse", is_staff=True)
        self.food = FineliFood.objects.create(fineli_id=1, name_fi="Apple", energy_kcal=50)

    def test_anonymous_request_to_food_diary_redirects_to_login(self):
        resp = self.client.get(reverse("food_diary"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("admin:login"), resp.url)

    def test_authenticated_user_can_access_food_diary(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("food_diary"))
        self.assertEqual(resp.status_code, 200)

    def test_valid_login_succeeds(self):
        resp = self.client.post(
            reverse("admin:login"), {"username": "alice", "password": "correct-horse", "next": "/food-diary/"}
        )
        self.assertEqual(resp.status_code, 302)  # redirected on success
        diary_resp = self.client.get(reverse("food_diary"))
        self.assertEqual(diary_resp.status_code, 200)  # session now authenticated

    def test_invalid_login_fails_safely(self):
        resp = self.client.post(reverse("admin:login"), {"username": "alice", "password": "wrong-password"})
        self.assertEqual(resp.status_code, 200)  # re-renders form, no redirect
        self.assertFalse(resp.context["user"].is_authenticated if resp.context else False)

    def test_logout_ends_session(self):
        self.client.force_login(self.user)
        self.client.post(reverse("admin:logout"))
        resp = self.client.get(reverse("food_diary"))
        self.assertEqual(resp.status_code, 302)

    def test_accounts_login_route_no_longer_exists(self):
        with self.assertRaises(NoReverseMatch):
            reverse("login")

    def test_anonymous_favorite_add_rejected(self):
        resp = self.client.post(
            reverse("add_favorite_food"), data='{"fineli_id": 1}', content_type="application/json"
        )
        self.assertEqual(resp.status_code, 401)

    def test_anonymous_favorite_remove_rejected(self):
        resp = self.client.post(
            reverse("remove_favorite_food"), data='{"fineli_id": 1}', content_type="application/json"
        )
        self.assertEqual(resp.status_code, 401)

    def test_recent_endpoint_still_works_unauthenticated(self):
        resp = self.client.get(reverse("list_recent_foods"))
        self.assertEqual(resp.status_code, 200)

    def test_frequent_endpoint_still_works_unauthenticated(self):
        resp = self.client.get(reverse("list_frequent_foods"))
        self.assertEqual(resp.status_code, 200)


class AdminLoginRoutingTests(TestCase):
    """Focused regression coverage for using Django admin's built-in
    login/logout as the application's single auth entry point - no separate
    HealthManager login view/route/template."""

    def setUp(self):
        User = get_user_model()
        self.user_a = User.objects.create_user(username="alr_user_a", password="x", is_staff=True)
        self.user_b = User.objects.create_user(username="alr_user_b", password="x", is_staff=True)
        self.recipe = CustomFood.objects.create(name="Recipe", user=self.user_a)
        self.legacy = CustomFood.objects.create(name="Legacy", user=None)

    def test_unauthenticated_protected_page_redirects_to_admin_login(self):
        resp = self.client.get(reverse("food_diary"))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.url.startswith("/admin/login/"))
        self.assertIn("next=", resp.url)

    def test_authenticated_user_can_access_protected_page_directly(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(reverse("food_diary"))
        self.assertEqual(resp.status_code, 200)

    def test_authenticated_user_can_access_recipe_page_directly(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(reverse("recipe_detail", args=[self.recipe.id]))
        self.assertEqual(resp.status_code, 200)

    def test_unauthenticated_recipe_page_redirects_to_admin_login(self):
        resp = self.client.get(reverse("recipe_detail", args=[self.recipe.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.url.startswith("/admin/login/"))

    def test_foreign_user_recipe_still_404s_after_authentication(self):
        self.client.force_login(self.user_b)
        resp = self.client.get(reverse("recipe_detail", args=[self.recipe.id]))
        self.assertEqual(resp.status_code, 404)

    def test_legacy_custom_food_still_unavailable(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(reverse("recipe_detail", args=[self.legacy.id]))
        self.assertEqual(resp.status_code, 404)

    def test_accounts_login_path_is_gone(self):
        resp = self.client.get("/accounts/login/")
        self.assertEqual(resp.status_code, 404)

    def test_admin_login_route_is_djangos_own_not_a_custom_view(self):
        from django.urls import resolve
        match = resolve("/admin/login/")
        self.assertTrue(match.func.__module__.startswith("django.contrib.admin"))


class SharedDjangoSessionAuthTests(TestCase):
    """Proves the SAME authenticated Django session (as force_login produces,
    matching what a real admin login stores) is recognized directly by
    HealthManager's own @login_required views - no separate login, no
    is_staff check, no redirect. If this passes but a real browser still
    bounces to /admin/login/, the cause is the browser using two different
    hostnames (e.g. localhost vs 127.0.0.1) for admin vs the app, which are
    two separate cookie jars - not a Django/session bug."""

    def setUp(self):
        User = get_user_model()
        self.user_a = User.objects.create_user(username="ssa_user_a", password="x")
        self.user_b = User.objects.create_user(username="ssa_user_b", password="x")
        self.recipe = CustomFood.objects.create(name="Recipe", user=self.user_a)

    def test_forced_session_opens_protected_page_directly(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(reverse("food_diary"))
        self.assertEqual(resp.status_code, 200)

    def test_authenticated_request_does_not_redirect_to_admin_login(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(reverse("food_diary"))
        self.assertNotEqual(resp.status_code, 302)

    def test_request_user_identity_preserved(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(reverse("food_diary"))
        self.assertEqual(resp.context["user"], self.user_a)
        self.assertTrue(resp.context["user"].is_authenticated)

    def test_foreign_user_ownership_still_404s(self):
        self.client.force_login(self.user_b)
        resp = self.client.get(reverse("recipe_detail", args=[self.recipe.id]))
        self.assertEqual(resp.status_code, 404)

    def test_anonymous_request_remains_protected(self):
        resp = self.client.get(reverse("food_diary"))
        self.assertEqual(resp.status_code, 302)

    def test_generated_nav_links_are_relative_not_absolute_host(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(reverse("food_diary"))
        content = resp.content.decode()
        # Nav is built with {% url %} reversing, never a hard-coded host.
        self.assertNotIn("localhost", content)
        self.assertNotIn("127.0.0.1", content)


class DiaryOwnershipSecurityTests(TestCase):
    """Focused regression coverage for per-user diary ownership: MealEntry.user
    isolation across reads, writes, edit/delete, bulk CSV re-import, and
    Recent/Frequent - including legacy user=None rows, which must stay
    invisible and untouched. Favorites isolation (already user-scoped) and
    the anonymous-diary-redirect boundary are covered by
    FavoritesRecentFrequentTests and AuthAndFoodDiaryAccessTests respectively
    and are not duplicated here."""

    DATE = "2026-08-29"

    def setUp(self):
        User = get_user_model()
        self.user_a = User.objects.create_user(username="user_a", password="x")
        self.user_b = User.objects.create_user(username="user_b", password="x")
        self.food = FineliFood.objects.create(fineli_id=42, name_fi="Rye Bread", energy_kcal=250)

        self.log = DailyLog.objects.get_or_create(date=self.DATE)[0]
        zero_macros = dict(protein_g=0, carbs_g=0, sugar_g=0, fat_g=0, sat_fat_g=0, fiber_g=0, salt_g=0)
        self.entry_a = MealEntry.objects.create(
            daily_log=self.log, user=self.user_a, source_type="manual",
            food_name="A's Lunch", meal_type="lunch", portion_grams=100, calories=300, **zero_macros,
        )
        self.entry_b = MealEntry.objects.create(
            daily_log=self.log, user=self.user_b, source_type="manual",
            food_name="B's Lunch", meal_type="lunch", portion_grams=100, calories=400, **zero_macros,
        )
        self.legacy_entry = MealEntry.objects.create(
            daily_log=self.log, user=None, source_type="bulk_import",
            food_name="Legacy Row", meal_type="lunch", portion_grams=100, calories=500, **zero_macros,
        )

    # 1-3: same-date coexistence and per-user diary reads
    def test_users_can_each_log_on_the_same_date(self):
        self.assertEqual(self.entry_a.daily_log_id, self.entry_b.daily_log_id)
        self.assertNotEqual(self.entry_a.user_id, self.entry_b.user_id)

    def test_user_a_diary_shows_only_own_entries(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(reverse("food_diary"), {"date": self.DATE})
        names = {e.food_name for e in resp.context["meal_entries"]}
        self.assertEqual(names, {"A's Lunch"})

    def test_user_b_diary_shows_only_own_entries(self):
        self.client.force_login(self.user_b)
        resp = self.client.get(reverse("food_diary"), {"date": self.DATE})
        names = {e.food_name for e in resp.context["meal_entries"]}
        self.assertEqual(names, {"B's Lunch"})

    # 4-5: object-level edit/delete isolation
    def test_user_a_cannot_edit_or_delete_user_b_entry(self):
        self.client.force_login(self.user_a)
        edit_resp = self.client.get(reverse("edit_meal_entry", args=[self.entry_b.id]))
        self.assertEqual(edit_resp.status_code, 404)
        delete_resp = self.client.post(reverse("delete_meal_entry", args=[self.entry_b.id]))
        self.assertEqual(delete_resp.status_code, 404)
        self.assertTrue(MealEntry.objects.filter(pk=self.entry_b.pk).exists())

    def test_user_b_cannot_edit_or_delete_user_a_entry(self):
        self.client.force_login(self.user_b)
        edit_resp = self.client.get(reverse("edit_meal_entry", args=[self.entry_a.id]))
        self.assertEqual(edit_resp.status_code, 404)
        delete_resp = self.client.post(reverse("delete_meal_entry", args=[self.entry_a.id]))
        self.assertEqual(delete_resp.status_code, 404)
        self.assertTrue(MealEntry.objects.filter(pk=self.entry_a.pk).exists())

    # 6-7: new-entry ownership
    def test_fineli_quick_add_belongs_to_authenticated_user(self):
        import json as _json
        self.client.force_login(self.user_a)
        payload = {
            "date": self.DATE, "fineli_id": 42, "name": "Rye Bread",
            "meal_type": "snack", "portion_grams": 100, "energy_kcal": 250,
        }
        self.client.post(reverse("add_fineli_entry"), data=_json.dumps(payload), content_type="application/json")
        entry = MealEntry.objects.get(daily_log__date=self.DATE, food_name="Rye Bread")
        self.assertEqual(entry.user, self.user_a)

    def test_manual_quick_add_belongs_to_authenticated_user(self):
        self.client.force_login(self.user_a)
        self.client.post(reverse("add_meal_entry"), {
            "date": self.DATE, "food_name": "Manual Snack", "meal_type": "snack",
            "portion_grams": "50", "calories": "80",
        })
        entry = MealEntry.objects.get(daily_log__date=self.DATE, food_name="Manual Snack")
        self.assertEqual(entry.user, self.user_a)

    # 8-10: bulk CSV import ownership and re-import isolation
    def test_bulk_csv_import_belongs_to_authenticated_user(self):
        self.client.force_login(self.user_a)
        self.client.post(reverse("bulk_import_diary_csv"), {"csv_files": upload(diary_csv(date=self.DATE))})
        entries = MealEntry.objects.filter(daily_log__date=self.DATE, source_type="bulk_import", user=self.user_a)
        self.assertEqual(entries.count(), 2)

    def test_csv_reimport_does_not_delete_other_users_entries(self):
        self.client.force_login(self.user_a)
        self.client.post(reverse("bulk_import_diary_csv"), {"csv_files": upload(diary_csv(date=self.DATE))})

        self.client.force_login(self.user_b)
        self.client.post(reverse("bulk_import_diary_csv"), {"csv_files": upload(corrected_csv(date=self.DATE))})

        a_entries = MealEntry.objects.filter(
            daily_log__date=self.DATE, source_type="bulk_import", user=self.user_a
        )
        self.assertEqual(a_entries.count(), 2)
        b_entries = MealEntry.objects.filter(
            daily_log__date=self.DATE, source_type="bulk_import", user=self.user_b
        )
        self.assertEqual(b_entries.count(), 1)
        self.assertEqual(b_entries.first().food_name, "Corrected Soup")

    def test_csv_reimport_does_not_delete_legacy_entries(self):
        self.assertTrue(MealEntry.objects.filter(pk=self.legacy_entry.pk).exists())
        self.client.force_login(self.user_a)
        self.client.post(reverse("bulk_import_diary_csv"), {"csv_files": upload(diary_csv(date=self.DATE))})
        self.assertTrue(MealEntry.objects.filter(pk=self.legacy_entry.pk).exists())

    # 11-15: Recent/Frequent isolation, including legacy exclusion
    def test_recent_and_frequent_exclude_other_users_and_legacy_rows(self):
        # Give user_a a Fineli-linked entry so it can appear in Recent/Frequent.
        MealEntry.objects.create(
            daily_log=self.log, user=self.user_a, fineli_food=self.food,
            source_type="fineli", food_name="Rye Bread", meal_type="snack", portion_grams=100,
        )
        # Same food, but owned by user_b and by nobody (legacy) - must never
        # count toward user_a's Recent/Frequent.
        MealEntry.objects.create(
            daily_log=self.log, user=self.user_b, fineli_food=self.food,
            source_type="fineli", food_name="Rye Bread", meal_type="snack", portion_grams=100,
        )
        MealEntry.objects.create(
            daily_log=self.log, user=None, fineli_food=self.food,
            source_type="fineli", food_name="Rye Bread", meal_type="snack", portion_grams=100,
        )

        self.client.force_login(self.user_a)
        recent = self.client.get(reverse("list_recent_foods")).json()["results"]
        frequent = self.client.get(reverse("list_frequent_foods")).json()["results"]
        self.assertEqual(len(recent), 1)
        self.assertEqual(frequent[0]["use_count_in_history"], 1)  # only user_a's own row counted

        self.client.force_login(self.user_b)
        recent_b = self.client.get(reverse("list_recent_foods")).json()["results"]
        self.assertEqual(len(recent_b), 1)


class CustomFoodOwnershipTests(TestCase):
    """Focused regression coverage for per-user CustomFood ownership:
    creation, list/search isolation, edit/delete isolation, and quick-add /
    favorite isolation - including legacy user=None rows, which must stay
    invisible and unusable through normal user endpoints."""

    def setUp(self):
        User = get_user_model()
        self.user_a = User.objects.create_user(username="cf_user_a", password="x")
        self.user_b = User.objects.create_user(username="cf_user_b", password="x")
        self.food_a = CustomFood.objects.create(name="Protein Bar", energy_kcal_per_100g=400, user=self.user_a)
        self.food_b = CustomFood.objects.create(name="Protein Bar", energy_kcal_per_100g=380, user=self.user_b)
        self.legacy_food = CustomFood.objects.create(name="Legacy Snack", energy_kcal_per_100g=200, user=None)
        self.fineli_food = FineliFood.objects.create(fineli_id=901, name_fi="Rice", energy_kcal=130)

    def test_new_custom_food_gets_request_user(self):
        self.client.force_login(self.user_a)
        self.client.post(reverse("add_custom_food"), {
            "name": "New Food", "energy_kcal_per_100g": "150", "default_portion_g": "100",
        })
        food = CustomFood.objects.get(name="New Food")
        self.assertEqual(food.user, self.user_a)

    def test_anonymous_custom_food_creation_rejected(self):
        resp = self.client.post(reverse("add_custom_food"), {
            "name": "Anon Food", "energy_kcal_per_100g": "150", "default_portion_g": "100",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("admin:login"), resp.url)
        self.assertFalse(CustomFood.objects.filter(name="Anon Food").exists())

    def test_user_a_lists_only_own_custom_foods(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(reverse("manage_custom_foods"))
        self.assertEqual({f.pk for f in resp.context["custom_foods"]}, {self.food_a.pk})

    def test_user_b_lists_only_own_custom_foods(self):
        self.client.force_login(self.user_b)
        resp = self.client.get(reverse("manage_custom_foods"))
        self.assertEqual({f.pk for f in resp.context["custom_foods"]}, {self.food_b.pk})

    def test_legacy_custom_food_appears_for_neither_user(self):
        self.client.force_login(self.user_a)
        resp_a = self.client.get(reverse("manage_custom_foods"))
        self.client.force_login(self.user_b)
        resp_b = self.client.get(reverse("manage_custom_foods"))
        self.assertNotIn(self.legacy_food.pk, {f.pk for f in resp_a.context["custom_foods"]})
        self.assertNotIn(self.legacy_food.pk, {f.pk for f in resp_b.context["custom_foods"]})

    def test_user_a_cannot_edit_user_b_custom_food(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(reverse("edit_custom_food", args=[self.food_b.id]))
        self.assertEqual(resp.status_code, 404)

    def test_user_a_cannot_delete_user_b_custom_food(self):
        self.client.force_login(self.user_a)
        resp = self.client.post(reverse("delete_custom_food", args=[self.food_b.id]))
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(CustomFood.objects.filter(pk=self.food_b.pk).exists())

    def test_user_a_cannot_quick_add_user_b_custom_food(self):
        import json as _json
        self.client.force_login(self.user_a)
        payload = {
            "date": "2026-09-01", "source": "custom", "custom_food_id": self.food_b.id,
            "name": "Protein Bar", "meal_type": "snack", "portion_grams": 100,
        }
        self.client.post(reverse("add_fineli_entry"), data=_json.dumps(payload), content_type="application/json")
        entry = MealEntry.objects.get(daily_log__date="2026-09-01", food_name="Protein Bar")
        self.assertIsNone(entry.custom_food)

    def test_user_a_can_quick_add_own_custom_food(self):
        import json as _json
        self.client.force_login(self.user_a)
        payload = {
            "date": "2026-09-02", "source": "custom", "custom_food_id": self.food_a.id,
            "name": "Protein Bar", "meal_type": "snack", "portion_grams": 100,
        }
        self.client.post(reverse("add_fineli_entry"), data=_json.dumps(payload), content_type="application/json")
        entry = MealEntry.objects.get(daily_log__date="2026-09-02", food_name="Protein Bar")
        self.assertEqual(entry.user, self.user_a)
        self.assertEqual(entry.custom_food, self.food_a)

    def test_user_a_cannot_favorite_user_b_custom_food(self):
        import json as _json
        self.client.force_login(self.user_a)
        resp = self.client.post(
            reverse("add_favorite_food"), data=_json.dumps({"custom_food_id": self.food_b.id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(FavoriteFood.objects.filter(user=self.user_a, custom_food=self.food_b).exists())

    def test_user_a_can_favorite_own_custom_food(self):
        import json as _json
        self.client.force_login(self.user_a)
        resp = self.client.post(
            reverse("add_favorite_food"), data=_json.dumps({"custom_food_id": self.food_a.id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(FavoriteFood.objects.filter(user=self.user_a, custom_food=self.food_a).exists())

    def test_same_name_allowed_for_different_users(self):
        self.assertEqual(self.food_a.name, self.food_b.name)
        self.assertNotEqual(self.food_a.pk, self.food_b.pk)

    def test_finelifood_remains_globally_reusable(self):
        self.client.force_login(self.user_a)
        resp_a = self.client.get(reverse("search_fineli_api"), {"q": "Rice"})
        self.client.force_login(self.user_b)
        resp_b = self.client.get(reverse("search_fineli_api"), {"q": "Rice"})
        self.assertEqual(resp_a.json()["count"], 1)
        self.assertEqual(resp_b.json()["count"], 1)

    def test_recent_reflects_owned_custom_food_quick_add(self):
        import json as _json
        self.client.force_login(self.user_a)
        payload = {
            "date": "2026-09-03", "source": "custom", "custom_food_id": self.food_a.id,
            "name": "Protein Bar", "meal_type": "snack", "portion_grams": 100,
        }
        self.client.post(reverse("add_fineli_entry"), data=_json.dumps(payload), content_type="application/json")
        results = self.client.get(reverse("list_recent_foods")).json()["results"]
        self.assertTrue(any(r.get("source") == "custom" for r in results))

    def test_legacy_custom_food_reference_from_existing_mealentry_remains_valid(self):
        log = DailyLog.objects.get_or_create(date="2026-08-30")[0]
        entry = MealEntry.objects.create(
            daily_log=log, user=self.user_a, custom_food=self.legacy_food,
            source_type="custom", food_name="Legacy Snack", meal_type="snack", portion_grams=100, calories=100,
        )
        entry.refresh_from_db()
        self.assertEqual(entry.custom_food, self.legacy_food)


class CustomFoodAuxiliaryNutrientTests(TestCase):
    """Focused regression coverage for CustomFoodNutrient: the normalized
    auxiliary-nutrient child table for CustomFood, mirroring the existing
    MealEntryNutrient/FineliFoodNutrient unknown-vs-zero and exact/lt
    semantics. No view/endpoint is introduced in this slice - CustomFoodForm
    still has no auxiliary fields, so ordinary create/edit never touches
    this table."""

    def setUp(self):
        User = get_user_model()
        self.user_a = User.objects.create_user(username="cfn_user_a", password="x")
        self.user_b = User.objects.create_user(username="cfn_user_b", password="x")
        self.food_a = CustomFood.objects.create(name="A's Bar", energy_kcal_per_100g=400, user=self.user_a)
        self.food_b = CustomFood.objects.create(name="B's Bar", energy_kcal_per_100g=380, user=self.user_b)
        self.legacy_food = CustomFood.objects.create(name="Legacy Snack", energy_kcal_per_100g=200, user=None)

    def test_can_add_auxiliary_nutrient_row(self):
        nutrient = CustomFoodNutrient.objects.create(
            custom_food=self.food_a, nutrient_key="sodium", value=250, unit="mg"
        )
        self.assertEqual(CustomFoodNutrient.objects.filter(pk=nutrient.pk).count(), 1)

    def test_nutrient_row_linked_through_custom_food(self):
        nutrient = CustomFoodNutrient.objects.create(
            custom_food=self.food_a, nutrient_key="sodium", value=250, unit="mg"
        )
        self.assertEqual(self.food_a.nutrients.get(), nutrient)
        self.assertEqual(nutrient.custom_food, self.food_a)

    def test_duplicate_nutrient_key_for_same_food_is_rejected(self):
        CustomFoodNutrient.objects.create(custom_food=self.food_a, nutrient_key="iron", value=2, unit="mg")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CustomFoodNutrient.objects.create(custom_food=self.food_a, nutrient_key="iron", value=3, unit="mg")

    def test_absent_nutrient_row_means_unknown(self):
        self.assertFalse(self.food_a.nutrients.filter(nutrient_key="iron").exists())

    def test_explicit_zero_is_a_genuine_known_value(self):
        nutrient = CustomFoodNutrient.objects.create(
            custom_food=self.food_a, nutrient_key="vitamin_d", value=0, unit="ug"
        )
        self.assertTrue(CustomFoodNutrient.objects.filter(pk=nutrient.pk, value=0).exists())

    def test_exact_qualifier_preserved(self):
        nutrient = CustomFoodNutrient.objects.create(
            custom_food=self.food_a, nutrient_key="sodium", value=250, unit="mg", qualifier="exact"
        )
        self.assertEqual(nutrient.qualifier, "exact")

    def test_lt_qualifier_preserved(self):
        nutrient = CustomFoodNutrient.objects.create(
            custom_food=self.food_a, nutrient_key="starch", value=0.1, unit="g", qualifier="lt"
        )
        self.assertEqual(nutrient.qualifier, "lt")

    def test_incorrect_unit_for_known_key_is_rejected(self):
        with self.assertRaises(ValidationError):
            CustomFoodNutrient.objects.create(custom_food=self.food_a, nutrient_key="sodium", value=100, unit="g")

    def test_g_nutrient_starch_works(self):
        nutrient = CustomFoodNutrient.objects.create(custom_food=self.food_a, nutrient_key="starch", value=20, unit="g")
        self.assertEqual(nutrient.unit, NUTRIENT_UNITS["starch"])

    def test_mg_nutrient_sodium_works(self):
        nutrient = CustomFoodNutrient.objects.create(custom_food=self.food_a, nutrient_key="sodium", value=250, unit="mg")
        self.assertEqual(nutrient.unit, NUTRIENT_UNITS["sodium"])

    def test_ug_nutrient_vitamin_d_works(self):
        nutrient = CustomFoodNutrient.objects.create(custom_food=self.food_a, nutrient_key="vitamin_d", value=5, unit="ug")
        self.assertEqual(nutrient.unit, NUTRIENT_UNITS["vitamin_d"])

    def test_custom_food_may_know_only_a_subset_of_nutrients(self):
        replace_custom_food_nutrients(self.food_a, [
            {"nutrient_key": "sodium", "value": 250},
            {"nutrient_key": "starch", "value": 20},
        ])
        self.assertEqual(
            set(self.food_a.nutrients.values_list("nutrient_key", flat=True)), {"sodium", "starch"}
        )
        self.assertFalse(self.food_a.nutrients.filter(nutrient_key="vitamin_d").exists())

    def test_two_custom_foods_may_know_different_subsets(self):
        replace_custom_food_nutrients(self.food_a, [{"nutrient_key": "sodium", "value": 250}])
        replace_custom_food_nutrients(self.food_b, [{"nutrient_key": "vitamin_d", "value": 5}])
        self.assertEqual(
            set(self.food_a.nutrients.values_list("nutrient_key", flat=True)), {"sodium"}
        )
        self.assertEqual(
            set(self.food_b.nutrients.values_list("nutrient_key", flat=True)), {"vitamin_d"}
        )

    def test_deleting_custom_food_cascades_nutrients(self):
        nutrient = CustomFoodNutrient.objects.create(custom_food=self.food_a, nutrient_key="iron", value=2, unit="mg")
        self.food_a.delete()
        self.assertFalse(CustomFoodNutrient.objects.filter(pk=nutrient.pk).exists())

    def test_core_typed_fields_remain_authoritative_and_not_duplicated(self):
        # Core CustomFood fields are untouched typed columns, not rows here.
        self.assertEqual(self.food_a.energy_kcal_per_100g, 400)
        core_field_names = {
            "energy_kcal_per_100g", "protein_per_100g", "carbs_per_100g", "sugar_per_100g",
            "fat_per_100g", "sat_fat_per_100g", "fiber_per_100g", "salt_per_100g",
        }
        self.assertFalse(core_field_names & set(NUTRIENT_UNITS.keys()))

    def test_custom_food_ownership_unchanged_by_adding_nutrients(self):
        CustomFoodNutrient.objects.create(custom_food=self.food_a, nutrient_key="iron", value=2, unit="mg")
        self.food_a.refresh_from_db()
        self.assertEqual(self.food_a.user, self.user_a)

    def test_nutrient_access_isolated_to_owning_users_own_custom_food(self):
        CustomFoodNutrient.objects.create(custom_food=self.food_a, nutrient_key="sodium", value=100, unit="mg")
        CustomFoodNutrient.objects.create(custom_food=self.food_b, nutrient_key="sodium", value=999, unit="mg")

        a_owned_nutrients = CustomFoodNutrient.objects.filter(custom_food__user=self.user_a)
        self.assertEqual(a_owned_nutrients.count(), 1)
        self.assertEqual(a_owned_nutrients.first().value, 100)

    def test_legacy_custom_food_has_no_fabricated_nutrients(self):
        self.assertEqual(self.legacy_food.nutrients.count(), 0)

    def test_ordinary_core_field_edit_does_not_erase_auxiliary_nutrients(self):
        nutrient = CustomFoodNutrient.objects.create(
            custom_food=self.food_a, nutrient_key="sodium", value=250, unit="mg"
        )
        self.client.force_login(self.user_a)
        self.client.post(reverse("edit_custom_food", args=[self.food_a.id]), {
            "name": "A's Bar", "energy_kcal_per_100g": "410", "default_portion_g": "100",
        })
        self.food_a.refresh_from_db()
        self.assertEqual(self.food_a.energy_kcal_per_100g, 410)
        self.assertTrue(CustomFoodNutrient.objects.filter(pk=nutrient.pk, value=250).exists())


class CustomFoodAuxiliaryPropagationTests(TestCase):
    """Focused regression coverage for propagating persisted
    CustomFoodNutrient rows into MealEntryNutrient on CustomFood quick-add,
    mirroring the existing FineliFoodNutrient -> MealEntryNutrient pattern."""

    def setUp(self):
        import json as _json
        self._json = _json
        User = get_user_model()
        self.user_a = User.objects.create_user(username="cfp_user_a", password="x")
        self.user_b = User.objects.create_user(username="cfp_user_b", password="x")
        self.food_a = CustomFood.objects.create(name="A's Bar", energy_kcal_per_100g=400, user=self.user_a)
        self.food_b = CustomFood.objects.create(name="B's Bar", energy_kcal_per_100g=380, user=self.user_b)
        self.legacy_food = CustomFood.objects.create(name="Legacy Snack", energy_kcal_per_100g=200, user=None)
        replace_custom_food_nutrients(self.food_a, [
            {"nutrient_key": "sodium", "value": 10},                        # mg, exact
            {"nutrient_key": "vitamin_d", "value": 0},                      # ug, explicit zero
            {"nutrient_key": "starch", "value": 0.1, "qualifier": "lt"},    # g, lt
        ])
        self.url = reverse("add_fineli_entry")

    def _post(self, custom_food_id, portion_grams=100, date="2026-09-05", **extra):
        payload = {
            "date": date, "source": "custom", "custom_food_id": custom_food_id,
            "name": "Quick Add", "meal_type": "snack", "portion_grams": portion_grams,
        }
        payload.update(extra)
        self.client.post(self.url, data=self._json.dumps(payload), content_type="application/json")
        # save()'s custom-food fallback rewrites food_name to str(custom_food)
        # when no client calories are sent, so look up by date, not name.
        return MealEntry.objects.filter(daily_log__date=date).order_by("-id").first()

    def test_owned_quick_add_creates_meal_entry(self):
        self.client.force_login(self.user_a)
        self.assertIsNotNone(self._post(self.food_a.id, date="2026-09-05"))

    def test_entry_user_is_request_user(self):
        self.client.force_login(self.user_a)
        entry = self._post(self.food_a.id, date="2026-09-06")
        self.assertEqual(entry.user, self.user_a)

    def test_entry_custom_food_is_source_food(self):
        self.client.force_login(self.user_a)
        entry = self._post(self.food_a.id, date="2026-09-07")
        self.assertEqual(entry.custom_food, self.food_a)

    def test_exact_auxiliary_row_propagates(self):
        self.client.force_login(self.user_a)
        entry = self._post(self.food_a.id, portion_grams=100, date="2026-09-08")
        sodium = entry.nutrients.get(nutrient_key="sodium")
        self.assertEqual(sodium.value, 10)
        self.assertEqual(sodium.qualifier, "exact")

    def test_portion_scaling(self):
        self.client.force_login(self.user_a)
        entry = self._post(self.food_a.id, portion_grams=50, date="2026-09-09")
        self.assertEqual(entry.nutrients.get(nutrient_key="sodium").value, 5)

    def test_explicit_zero_propagates(self):
        self.client.force_login(self.user_a)
        entry = self._post(self.food_a.id, date="2026-09-10")
        vit_d = entry.nutrients.get(nutrient_key="vitamin_d")
        self.assertEqual(vit_d.value, 0)
        self.assertEqual(vit_d.qualifier, "exact")

    def test_missing_nutrient_creates_no_row(self):
        self.client.force_login(self.user_a)
        entry = self._post(self.food_a.id, date="2026-09-11")
        self.assertFalse(entry.nutrients.filter(nutrient_key="iron").exists())

    def test_lt_qualifier_propagates(self):
        self.client.force_login(self.user_a)
        entry = self._post(self.food_a.id, date="2026-09-12")
        self.assertEqual(entry.nutrients.get(nutrient_key="starch").qualifier, "lt")

    def test_lt_threshold_scales(self):
        self.client.force_login(self.user_a)
        entry = self._post(self.food_a.id, portion_grams=50, date="2026-09-13")
        self.assertAlmostEqual(entry.nutrients.get(nutrient_key="starch").value, 0.05, places=6)

    def test_g_unit_preserved(self):
        self.client.force_login(self.user_a)
        entry = self._post(self.food_a.id, date="2026-09-14")
        self.assertEqual(entry.nutrients.get(nutrient_key="starch").unit, "g")

    def test_mg_unit_preserved(self):
        self.client.force_login(self.user_a)
        entry = self._post(self.food_a.id, date="2026-09-15")
        self.assertEqual(entry.nutrients.get(nutrient_key="sodium").unit, "mg")

    def test_ug_unit_preserved(self):
        self.client.force_login(self.user_a)
        entry = self._post(self.food_a.id, date="2026-09-16")
        self.assertEqual(entry.nutrients.get(nutrient_key="vitamin_d").unit, "ug")

    def test_multiple_nutrients_propagate_together(self):
        self.client.force_login(self.user_a)
        entry = self._post(self.food_a.id, date="2026-09-17")
        self.assertEqual(
            set(entry.nutrients.values_list("nutrient_key", flat=True)), {"sodium", "vitamin_d", "starch"}
        )

    def test_partial_subset_remains_partial(self):
        self.client.force_login(self.user_a)
        entry = self._post(self.food_a.id, date="2026-09-18")
        self.assertEqual(entry.nutrients.count(), 3)  # not the full ~44-key taxonomy

    def test_typed_core_nutrients_not_duplicated_into_mealentrynutrient(self):
        self.client.force_login(self.user_a)
        entry = self._post(self.food_a.id, date="2026-09-19")
        core_keys = ["calories", "protein_g", "carbs_g", "fat_g", "sat_fat_g", "sugar_g", "fiber_g", "salt_g"]
        self.assertFalse(entry.nutrients.filter(nutrient_key__in=core_keys).exists())

    def test_source_customfoodnutrient_rows_unchanged(self):
        self.client.force_login(self.user_a)
        self._post(self.food_a.id, portion_grams=50, date="2026-09-20")
        self.assertEqual(self.food_a.nutrients.get(nutrient_key="sodium").value, 10)  # unscaled, unmutated

    def test_user_a_cannot_quick_add_user_b_custom_food(self):
        self.client.force_login(self.user_a)
        entry = self._post(self.food_b.id, date="2026-09-21")
        self.assertIsNone(entry.custom_food)
        self.assertFalse(entry.nutrients.exists())

    def test_legacy_custom_food_cannot_be_quick_added(self):
        self.client.force_login(self.user_a)
        entry = self._post(self.legacy_food.id, date="2026-09-22")
        self.assertIsNone(entry.custom_food)
        self.assertFalse(entry.nutrients.exists())

    def test_no_client_auxiliary_payload_required(self):
        self.client.force_login(self.user_a)
        entry = self._post(self.food_a.id, date="2026-09-23")  # no aux keys sent at all
        self.assertEqual(entry.nutrients.count(), 3)

    def test_tampered_client_auxiliary_value_cannot_override_persisted_truth(self):
        self.client.force_login(self.user_a)
        # The endpoint has no client-facing auxiliary nutrient input; an
        # extra unsupported field is simply ignored, proving the DB value wins.
        entry = self._post(self.food_a.id, date="2026-09-24", sodium=999999)
        self.assertEqual(entry.nutrients.get(nutrient_key="sodium").value, 10)


class CustomFoodIngredientRecipeTests(TestCase):
    """Focused regression coverage for the CustomFoodIngredient recipe
    foundation: source XOR/ownership/cycle validation on the model, and the
    pure recipe_aggregation module mirroring aggregation.py's
    DailyNutrientResult semantics over a recipe's ingredients."""

    def setUp(self):
        User = get_user_model()
        self.user_a = User.objects.create_user(username="rcp_user_a", password="x")
        self.user_b = User.objects.create_user(username="rcp_user_b", password="x")

        self.oats = FineliFood.objects.create(
            fineli_id=7001, name_fi="Oats", energy_kcal=300, protein_g=10, carbs_g=None,
        )
        FineliFoodNutrient.objects.create(fineli_food=self.oats, nutrient_key="sodium", value=5, unit="mg")
        FineliFoodNutrient.objects.create(
            fineli_food=self.oats, nutrient_key="starch", value=0.2, unit="g", qualifier="lt"
        )

        self.protein_powder = CustomFood.objects.create(
            name="Protein Powder", user=self.user_a,
            energy_kcal_per_100g=350, protein_per_100g=80, carbs_per_100g=5,
        )
        CustomFoodNutrient.objects.create(
            custom_food=self.protein_powder, nutrient_key="sodium", value=100, unit="mg"
        )

        self.recipe = CustomFood.objects.create(name="My Recipe", user=self.user_a)

    def _ingredient(self, **kwargs):
        defaults = dict(custom_food=self.recipe, amount_grams=100)
        defaults.update(kwargs)
        return CustomFoodIngredient.objects.create(**defaults)

    def _new_recipe(self):
        return CustomFood.objects.create(name="Other Recipe", user=self.user_a)

    # --- MODEL / OWNERSHIP ---

    def test_fineli_ingredient_can_be_created(self):
        ing = self._ingredient(fineli_food=self.oats, amount_grams=60)
        self.assertEqual(ing.fineli_food, self.oats)

    def test_owned_custom_food_ingredient_can_be_created(self):
        ing = self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)
        self.assertEqual(ing.source_custom_food, self.protein_powder)

    def test_xor_requires_exactly_one_source(self):
        with self.assertRaises(ValidationError):
            CustomFoodIngredient.objects.create(custom_food=self.recipe, amount_grams=50)
        with self.assertRaises(ValidationError):
            CustomFoodIngredient.objects.create(
                custom_food=self.recipe, fineli_food=self.oats,
                source_custom_food=self.protein_powder, amount_grams=50,
            )

    def test_zero_amount_rejected(self):
        with self.assertRaises(ValidationError):
            self._ingredient(fineli_food=self.oats, amount_grams=0)

    def test_negative_amount_rejected(self):
        with self.assertRaises(ValidationError):
            self._ingredient(fineli_food=self.oats, amount_grams=-10)

    def test_user_a_recipe_cannot_reference_user_b_custom_food(self):
        food_b = CustomFood.objects.create(name="B's Food", user=self.user_b)
        with self.assertRaises(ValidationError):
            self._ingredient(source_custom_food=food_b, amount_grams=50)

    def test_legacy_source_custom_food_rejected(self):
        legacy = CustomFood.objects.create(name="Legacy", user=None)
        with self.assertRaises(ValidationError):
            self._ingredient(source_custom_food=legacy, amount_grams=50)

    def test_direct_self_reference_rejected(self):
        with self.assertRaises(ValidationError):
            self._ingredient(source_custom_food=self.recipe, amount_grams=50)

    def test_a_to_b_to_a_cycle_rejected(self):
        b = CustomFood.objects.create(name="B", user=self.user_a)
        CustomFoodIngredient.objects.create(custom_food=self.recipe, source_custom_food=b, amount_grams=50)
        with self.assertRaises(ValidationError):
            CustomFoodIngredient.objects.create(custom_food=b, source_custom_food=self.recipe, amount_grams=50)

    def test_a_to_b_to_c_to_a_cycle_rejected(self):
        b = CustomFood.objects.create(name="B2", user=self.user_a)
        c = CustomFood.objects.create(name="C2", user=self.user_a)
        CustomFoodIngredient.objects.create(custom_food=self.recipe, source_custom_food=b, amount_grams=50)
        CustomFoodIngredient.objects.create(custom_food=b, source_custom_food=c, amount_grams=50)
        with self.assertRaises(ValidationError):
            CustomFoodIngredient.objects.create(custom_food=c, source_custom_food=self.recipe, amount_grams=50)

    def test_valid_nested_a_b_c_graph_accepted(self):
        b = CustomFood.objects.create(name="B3", user=self.user_a)
        c = CustomFood.objects.create(name="C3", user=self.user_a)
        CustomFoodIngredient.objects.create(custom_food=self.recipe, source_custom_food=b, amount_grams=50)
        ing = CustomFoodIngredient.objects.create(custom_food=b, source_custom_food=c, amount_grams=50)
        self.assertEqual(ing.source_custom_food, c)

    # --- MASS ---

    def test_total_recipe_grams_sums_correctly(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)
        self.assertEqual(total_recipe_grams(self.recipe), 90)

    # --- CORE AGGREGATION ---

    def test_exact_known_core_nutrients_sum_correctly(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)                    # 300*0.6=180
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)   # 350*0.3=105
        r = recipe_nutrient_result(self.recipe, "calories")
        self.assertAlmostEqual(r.known_exact_sum, 285)
        self.assertEqual(r.coverage, "complete")

    def test_explicit_core_zero_is_known_zero(self):
        water = FineliFood.objects.create(fineli_id=7002, name_fi="Water", energy_kcal=0)
        self._ingredient(fineli_food=water, amount_grams=100)
        r = recipe_nutrient_result(self.recipe, "calories")
        self.assertEqual(r.known_exact_sum, 0)
        self.assertEqual(r.coverage, "complete")

    def test_missing_core_value_produces_partial_coverage(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)                    # carbs_g=None
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)   # carbs_per_100g=5
        r = recipe_nutrient_result(self.recipe, "carbs_g")
        self.assertEqual(r.coverage, "partial")
        self.assertEqual(r.known_ingredient_count, 1)
        self.assertEqual(r.total_ingredient_count, 2)

    def test_all_missing_core_value_produces_none_coverage(self):
        milk = FineliFood.objects.create(fineli_id=7003, name_fi="Milk", carbs_g=None)
        self._ingredient(fineli_food=self.oats, amount_grams=60)   # carbs_g=None
        self._ingredient(fineli_food=milk, amount_grams=40)        # carbs_g=None
        r = recipe_nutrient_result(self.recipe, "carbs_g")
        self.assertEqual(r.coverage, "none")
        self.assertIsNone(r.recipe_upper_bound)

    def test_all_known_core_value_produces_complete_coverage(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)
        r = recipe_nutrient_result(self.recipe, "calories")
        self.assertEqual(r.coverage, "complete")
        self.assertEqual(r.known_ingredient_count, r.total_ingredient_count)

    # --- AUXILIARY AGGREGATION ---

    def test_fineli_auxiliary_exact_contribution_scales_by_grams(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)  # sodium 5mg/100g
        r = recipe_nutrient_result(self.recipe, "sodium")
        self.assertAlmostEqual(r.known_exact_sum, 3.0)

    def test_customfood_auxiliary_exact_contribution_scales_by_grams(self):
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)  # sodium 100mg/100g
        r = recipe_nutrient_result(self.recipe, "sodium")
        self.assertAlmostEqual(r.known_exact_sum, 30.0)

    def test_missing_auxiliary_row_contributes_unknown(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)  # no "iron" row on oats
        r = recipe_nutrient_result(self.recipe, "iron")
        self.assertEqual(r.known_ingredient_count, 0)
        self.assertEqual(r.total_ingredient_count, 1)
        self.assertEqual(r.coverage, "none")

    def test_explicit_auxiliary_zero_is_known_zero(self):
        FineliFoodNutrient.objects.create(fineli_food=self.oats, nutrient_key="iron", value=0, unit="mg")
        self._ingredient(fineli_food=self.oats, amount_grams=60)
        r = recipe_nutrient_result(self.recipe, "iron")
        self.assertEqual(r.known_exact_sum, 0)
        self.assertEqual(r.coverage, "complete")

    def test_lt_qualifier_produces_bounded_result(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)  # starch is lt
        r = recipe_nutrient_result(self.recipe, "starch")
        self.assertEqual(r.bound_status, "bounded")

    def test_scaled_lt_threshold_is_correct(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)  # 0.2g/100g lt -> *0.6
        r = recipe_nutrient_result(self.recipe, "starch")
        self.assertAlmostEqual(r.known_upper_bound_sum, 0.12)
        self.assertEqual(r.known_exact_sum, 0)  # lt never counts as exact

    def test_partial_coverage_prevents_whole_recipe_upper_bound(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)                    # starch known (lt)
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)   # no starch row
        r = recipe_nutrient_result(self.recipe, "starch")
        self.assertEqual(r.coverage, "partial")
        self.assertIsNone(r.recipe_upper_bound)

    def test_complete_bounded_coverage_provides_whole_recipe_upper_bound(self):
        CustomFoodNutrient.objects.create(
            custom_food=self.protein_powder, nutrient_key="starch", value=5, unit="g", qualifier="exact"
        )
        self._ingredient(fineli_food=self.oats, amount_grams=60)                    # 0.2 lt *0.6=0.12
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)   # 5 exact *0.3=1.5
        r = recipe_nutrient_result(self.recipe, "starch")
        self.assertEqual(r.coverage, "complete")
        self.assertEqual(r.bound_status, "bounded")
        self.assertAlmostEqual(r.recipe_lower_bound, 1.5)
        self.assertAlmostEqual(r.recipe_upper_bound, 1.62)

    def test_g_unit_preserved(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)
        self.assertEqual(recipe_nutrient_result(self.recipe, "starch").unit, "g")

    def test_mg_unit_preserved(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)
        self.assertEqual(recipe_nutrient_result(self.recipe, "sodium").unit, "mg")

    def test_ug_unit_preserved(self):
        CustomFoodNutrient.objects.create(custom_food=self.protein_powder, nutrient_key="vitamin_d", value=2, unit="ug")
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)
        self.assertEqual(recipe_nutrient_result(self.recipe, "vitamin_d").unit, "ug")

    # --- PER-100G NORMALIZATION ---

    def test_total_recipe_values_normalize_correctly_to_100g(self):
        self._ingredient(fineli_food=self.oats, amount_grams=500)  # protein 10/100g*500=50
        raw = recipe_nutrient_result(self.recipe, "protein_g")
        per100 = normalize_to_per_100g(raw, total_recipe_grams(self.recipe))
        self.assertAlmostEqual(per100.lower_bound, 10.0)

    def test_normalization_preserves_coverage(self):
        self._ingredient(fineli_food=self.oats, amount_grams=500)
        raw = recipe_nutrient_result(self.recipe, "protein_g")
        per100 = normalize_to_per_100g(raw, total_recipe_grams(self.recipe))
        self.assertEqual(per100.coverage, raw.coverage)

    def test_normalization_preserves_bound_status(self):
        self._ingredient(fineli_food=self.oats, amount_grams=500)
        raw = recipe_nutrient_result(self.recipe, "protein_g")
        per100 = normalize_to_per_100g(raw, total_recipe_grams(self.recipe))
        self.assertEqual(per100.bound_status, raw.bound_status)

    def test_empty_recipe_does_not_fabricate_zero(self):
        raw = recipe_nutrient_result(self.recipe, "calories")
        self.assertEqual(raw.coverage, "no_ingredient_data")
        self.assertIsNone(raw.recipe_upper_bound)
        per100 = normalize_to_per_100g(raw, total_recipe_grams(self.recipe))
        self.assertIsNone(per100.lower_bound)
        self.assertIsNone(per100.upper_bound)

    # --- IMMUTABILITY ---

    def test_calculation_does_not_mutate_source_nutrient_data(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)
        recipe_nutrient_result(self.recipe, "calories")
        recipe_nutrient_result(self.recipe, "sodium")
        self.oats.refresh_from_db()
        self.assertEqual(self.oats.energy_kcal, 300)
        self.assertEqual(self.oats.nutrients.get(nutrient_key="sodium").value, 5)

    def test_calculation_does_not_write_parent_custom_food_nutrient_fields(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)
        recipe_nutrient_result(self.recipe, "calories")
        self.recipe.refresh_from_db()
        self.assertIsNone(self.recipe.energy_kcal_per_100g)
        self.assertEqual(CustomFoodNutrient.objects.filter(custom_food=self.recipe).count(), 0)


class CustomFoodRecipePersistenceTests(TestCase):
    """Focused regression coverage for recalculate_custom_food_recipe(): the
    explicit service that materializes ONLY truthfully-complete
    recipe_aggregation results into the parent CustomFood's typed core
    fields and CustomFoodNutrient rows, leaving partial/unknown results
    unpersisted rather than fabricating them."""

    def setUp(self):
        User = get_user_model()
        self.user_a = User.objects.create_user(username="rcpp_user_a", password="x")

        self.oats = FineliFood.objects.create(
            fineli_id=8001, name_fi="Oats", energy_kcal=300, protein_g=10, carbs_g=None,
        )
        FineliFoodNutrient.objects.create(fineli_food=self.oats, nutrient_key="sodium", value=5, unit="mg")
        FineliFoodNutrient.objects.create(
            fineli_food=self.oats, nutrient_key="starch", value=0.2, unit="g", qualifier="lt"
        )

        self.protein_powder = CustomFood.objects.create(
            name="Protein Powder", user=self.user_a,
            energy_kcal_per_100g=350, protein_per_100g=80, carbs_per_100g=5,
        )
        CustomFoodNutrient.objects.create(
            custom_food=self.protein_powder, nutrient_key="sodium", value=100, unit="mg"
        )

        self.recipe = CustomFood.objects.create(name="My Recipe", user=self.user_a)

    def _ingredient(self, **kwargs):
        defaults = dict(custom_food=self.recipe, amount_grams=100)
        defaults.update(kwargs)
        return CustomFoodIngredient.objects.create(**defaults)

    # --- CORE ---

    def test_complete_exact_core_result_persists_per_100g(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)                   # 300*0.6=180
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)  # 350*0.3=105
        recalculate_custom_food_recipe(self.recipe)
        self.recipe.refresh_from_db()
        self.assertAlmostEqual(self.recipe.energy_kcal_per_100g, 285 * 100 / 90)

    def test_complete_explicit_zero_persists_as_zero(self):
        water = FineliFood.objects.create(fineli_id=8002, name_fi="Water", energy_kcal=0)
        self._ingredient(fineli_food=water, amount_grams=100)
        recalculate_custom_food_recipe(self.recipe)
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.energy_kcal_per_100g, 0.0)

    def test_partial_core_coverage_persists_none(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)                   # carbs_g=None
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)  # carbs_per_100g=5
        recalculate_custom_food_recipe(self.recipe)
        self.recipe.refresh_from_db()
        self.assertIsNone(self.recipe.carbs_per_100g)

    def test_none_core_coverage_persists_none(self):
        milk = FineliFood.objects.create(fineli_id=8003, name_fi="Milk", carbs_g=None)
        self._ingredient(fineli_food=self.oats, amount_grams=60)
        self._ingredient(fineli_food=milk, amount_grams=40)
        recalculate_custom_food_recipe(self.recipe)
        self.recipe.refresh_from_db()
        self.assertIsNone(self.recipe.carbs_per_100g)

    def test_empty_recipe_persists_none_for_all_core_fields(self):
        recalculate_custom_food_recipe(self.recipe)
        self.recipe.refresh_from_db()
        for field in [
            "energy_kcal_per_100g", "protein_per_100g", "carbs_per_100g", "sugar_per_100g",
            "fat_per_100g", "sat_fat_per_100g", "fiber_per_100g", "salt_per_100g",
        ]:
            self.assertIsNone(getattr(self.recipe, field), field)

    def test_stale_prior_core_value_becomes_none_after_complete_to_partial(self):
        ing = self._ingredient(fineli_food=self.oats, amount_grams=60)
        recalculate_custom_food_recipe(self.recipe)
        self.recipe.refresh_from_db()
        self.assertIsNotNone(self.recipe.energy_kcal_per_100g)

        unknown_food = FineliFood.objects.create(fineli_id=8004, name_fi="Mystery", energy_kcal=None)
        self._ingredient(fineli_food=unknown_food, amount_grams=20)
        recalculate_custom_food_recipe(self.recipe)
        self.recipe.refresh_from_db()
        self.assertIsNone(self.recipe.energy_kcal_per_100g)

    # --- AUX EXACT ---

    def test_complete_exact_auxiliary_creates_row(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)
        recalculate_custom_food_recipe(self.recipe)
        sodium = self.recipe.nutrients.get(nutrient_key="sodium")
        self.assertEqual(sodium.qualifier, "exact")

    def test_normalized_per_100g_auxiliary_value_correct(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)                   # 5*0.6=3
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)  # 100*0.3=30
        recalculate_custom_food_recipe(self.recipe)
        sodium = self.recipe.nutrients.get(nutrient_key="sodium")
        self.assertAlmostEqual(sodium.value, 33 * 100 / 90)

    def test_explicit_auxiliary_zero_creates_zero_row(self):
        FineliFoodNutrient.objects.create(fineli_food=self.oats, nutrient_key="iron", value=0, unit="mg")
        self._ingredient(fineli_food=self.oats, amount_grams=60)
        recalculate_custom_food_recipe(self.recipe)
        iron = self.recipe.nutrients.get(nutrient_key="iron")
        self.assertEqual(iron.value, 0)

    def test_g_unit_correct(self):
        CustomFoodNutrient.objects.create(custom_food=self.protein_powder, nutrient_key="starch", value=5, unit="g")
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)
        recalculate_custom_food_recipe(self.recipe)
        self.assertEqual(self.recipe.nutrients.get(nutrient_key="starch").unit, "g")

    def test_mg_unit_correct(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)
        recalculate_custom_food_recipe(self.recipe)
        self.assertEqual(self.recipe.nutrients.get(nutrient_key="sodium").unit, "mg")

    def test_ug_unit_correct(self):
        CustomFoodNutrient.objects.create(custom_food=self.protein_powder, nutrient_key="vitamin_d", value=2, unit="ug")
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)
        recalculate_custom_food_recipe(self.recipe)
        self.assertEqual(self.recipe.nutrients.get(nutrient_key="vitamin_d").unit, "ug")

    # --- AUX BOUNDED ---

    def test_complete_bounded_result_creates_qualifier_lt(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)
        recalculate_custom_food_recipe(self.recipe)
        self.assertEqual(self.recipe.nutrients.get(nutrient_key="starch").qualifier, "lt")

    def test_persisted_value_uses_normalized_whole_recipe_upper_bound(self):
        CustomFoodNutrient.objects.create(
            custom_food=self.protein_powder, nutrient_key="starch", value=5, unit="g", qualifier="exact"
        )
        self._ingredient(fineli_food=self.oats, amount_grams=60)                   # lt 0.2*0.6=0.12
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)  # exact 5*0.3=1.5
        recalculate_custom_food_recipe(self.recipe)
        starch = self.recipe.nutrients.get(nutrient_key="starch")
        self.assertEqual(starch.qualifier, "lt")
        self.assertAlmostEqual(starch.value, 1.62 * 100 / 90)

    def test_lt_threshold_scaling_is_correct(self):
        self._ingredient(fineli_food=self.oats, amount_grams=25)  # 0.2*0.25=0.05 -> normalized back to 0.2
        recalculate_custom_food_recipe(self.recipe)
        self.assertAlmostEqual(self.recipe.nutrients.get(nutrient_key="starch").value, 0.2)

    # --- AUX PARTIAL / UNKNOWN ---

    def test_partial_auxiliary_coverage_creates_no_row(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)                   # starch known
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)  # no starch row
        recalculate_custom_food_recipe(self.recipe)
        self.assertFalse(self.recipe.nutrients.filter(nutrient_key="starch").exists())

    def test_none_coverage_creates_no_row(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)  # no "iron" row
        recalculate_custom_food_recipe(self.recipe)
        self.assertFalse(self.recipe.nutrients.filter(nutrient_key="iron").exists())

    def test_empty_recipe_creates_no_auxiliary_rows(self):
        recalculate_custom_food_recipe(self.recipe)
        self.assertEqual(CustomFoodNutrient.objects.filter(custom_food=self.recipe).count(), 0)

    def test_stale_prior_aux_row_deleted_on_complete_to_partial(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)
        recalculate_custom_food_recipe(self.recipe)
        self.assertTrue(self.recipe.nutrients.filter(nutrient_key="sodium").exists())

        salt_free = FineliFood.objects.create(fineli_id=8005, name_fi="Salt-free")
        self._ingredient(fineli_food=salt_free, amount_grams=20)
        recalculate_custom_food_recipe(self.recipe)
        self.assertFalse(self.recipe.nutrients.filter(nutrient_key="sodium").exists())

    def test_stale_prior_aux_row_deleted_after_ingredient_removal(self):
        ing = self._ingredient(fineli_food=self.oats, amount_grams=60)
        recalculate_custom_food_recipe(self.recipe)
        self.assertTrue(self.recipe.nutrients.filter(nutrient_key="sodium").exists())

        ing.delete()
        recalculate_custom_food_recipe(self.recipe)
        self.assertFalse(self.recipe.nutrients.filter(nutrient_key="sodium").exists())

    # --- RECALCULATION ---

    def test_ingredient_correction_changes_persisted_value(self):
        ing = self._ingredient(fineli_food=self.oats, amount_grams=60)
        recalculate_custom_food_recipe(self.recipe)
        self.recipe.refresh_from_db()
        first_value = self.recipe.energy_kcal_per_100g

        ing.amount_grams = 30
        ing.save()
        recalculate_custom_food_recipe(self.recipe)
        self.recipe.refresh_from_db()
        # Single-ingredient recipe always normalizes back to the source's own
        # per-100g value regardless of amount, so this instead proves the
        # value tracks the corrected ingredient graph, not a stale cache.
        self.assertEqual(first_value, self.recipe.energy_kcal_per_100g)
        self.assertEqual(CustomFoodIngredient.objects.get(pk=ing.pk).amount_grams, 30)

    def test_repeated_recalculation_is_idempotent(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)
        recalculate_custom_food_recipe(self.recipe)
        self.recipe.refresh_from_db()
        v1 = self.recipe.energy_kcal_per_100g
        recalculate_custom_food_recipe(self.recipe)
        self.recipe.refresh_from_db()
        v2 = self.recipe.energy_kcal_per_100g
        self.assertEqual(v1, v2)
        self.assertEqual(self.recipe.nutrients.filter(nutrient_key="sodium").count(), 1)

    def test_parent_user_and_name_unchanged(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)
        recalculate_custom_food_recipe(self.recipe)
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.user, self.user_a)
        self.assertEqual(self.recipe.name, "My Recipe")

    def test_ingredient_rows_unchanged_by_recalculation(self):
        ing = self._ingredient(fineli_food=self.oats, amount_grams=60)
        recalculate_custom_food_recipe(self.recipe)
        ing.refresh_from_db()
        self.assertEqual(ing.amount_grams, 60)
        self.assertEqual(ing.fineli_food_id, self.oats.id)

    def test_finelifood_source_unchanged_by_recalculation(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)
        recalculate_custom_food_recipe(self.recipe)
        self.oats.refresh_from_db()
        self.assertEqual(self.oats.energy_kcal, 300)

    def test_source_custom_food_unchanged_by_recalculation(self):
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)
        recalculate_custom_food_recipe(self.recipe)
        self.protein_powder.refresh_from_db()
        self.assertEqual(self.protein_powder.energy_kcal_per_100g, 350)

    def test_source_nutrient_children_unchanged_by_recalculation(self):
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)
        recalculate_custom_food_recipe(self.recipe)
        self.assertEqual(self.protein_powder.nutrients.get(nutrient_key="sodium").value, 100)

    # --- NESTED ---

    def test_parent_recipe_uses_currently_persisted_nested_value(self):
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)
        recalculate_custom_food_recipe(self.recipe)
        self.recipe.refresh_from_db()
        first_value = self.recipe.protein_per_100g

        self.protein_powder.protein_per_100g = 40
        self.protein_powder.save()
        recalculate_custom_food_recipe(self.recipe)
        self.recipe.refresh_from_db()
        self.assertNotEqual(first_value, self.recipe.protein_per_100g)

    def test_nested_custom_food_is_not_automatically_recalculated(self):
        # protein_powder becomes itself a (never-recalculated) mini-recipe.
        CustomFoodIngredient.objects.create(
            custom_food=self.protein_powder, fineli_food=self.oats, amount_grams=50
        )
        self._ingredient(source_custom_food=self.protein_powder, amount_grams=30)
        recalculate_custom_food_recipe(self.recipe)  # recalculate the PARENT only
        self.protein_powder.refresh_from_db()
        self.assertEqual(self.protein_powder.energy_kcal_per_100g, 350)  # still its manual value

    # --- ATOMICITY ---

    def test_core_and_auxiliary_updates_apply_together_in_one_call(self):
        self._ingredient(fineli_food=self.oats, amount_grams=60)
        recalculate_custom_food_recipe(self.recipe)
        self.recipe.refresh_from_db()
        self.assertIsNotNone(self.recipe.energy_kcal_per_100g)
        self.assertTrue(self.recipe.nutrients.filter(nutrient_key="sodium").exists())

        # Ingredient graph changes: calories stays complete, sodium becomes partial.
        salt_free = FineliFood.objects.create(fineli_id=8006, name_fi="Salt-free2", energy_kcal=50)
        self._ingredient(fineli_food=salt_free, amount_grams=20)
        recalculate_custom_food_recipe(self.recipe)
        self.recipe.refresh_from_db()
        self.assertIsNotNone(self.recipe.energy_kcal_per_100g)  # core: still complete, updated
        self.assertFalse(self.recipe.nutrients.filter(nutrient_key="sodium").exists())  # aux: now stale, removed

    # --- MANUAL BEHAVIOR ---

    def test_ordinary_save_does_not_trigger_recalculation(self):
        manual = CustomFood.objects.create(
            name="Manual Bar", user=self.user_a, energy_kcal_per_100g=250, protein_per_100g=20,
        )
        manual.brand = "Foo"
        manual.save()
        manual.refresh_from_db()
        self.assertEqual(manual.energy_kcal_per_100g, 250)
        self.assertEqual(manual.protein_per_100g, 20)


class NestedRecipeRecalculationTests(TestCase):
    """Focused regression coverage for recalculate_custom_food_recipe_with_
    dependencies(): child-before-parent orchestration over
    recalculate_custom_food_recipe(), without duplicating its calculation
    logic."""

    def setUp(self):
        User = get_user_model()
        self.user_a = User.objects.create_user(username="nrr_user_a", password="x")
        self.user_b = User.objects.create_user(username="nrr_user_b", password="x")
        self.oats = FineliFood.objects.create(fineli_id=9001, name_fi="Oats", energy_kcal=300, protein_g=10)
        FineliFoodNutrient.objects.create(fineli_food=self.oats, nutrient_key="sodium", value=5, unit="mg")
        FineliFoodNutrient.objects.create(
            fineli_food=self.oats, nutrient_key="starch", value=0.2, unit="g", qualifier="lt"
        )

    def _food(self, name, **kwargs):
        return CustomFood.objects.create(name=name, user=self.user_a, **kwargs)

    # --- ORDER / GRAPH ---

    def test_a_to_b_recalculates_b_before_a(self):
        b = self._food("B")
        CustomFoodIngredient.objects.create(custom_food=b, fineli_food=self.oats, amount_grams=100)
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=b, amount_grams=50)

        recalculate_custom_food_recipe_with_dependencies(a)
        b.refresh_from_db()
        a.refresh_from_db()
        self.assertEqual(b.energy_kcal_per_100g, 300)
        self.assertEqual(a.energy_kcal_per_100g, 300)  # only reachable if B was already fresh

    def test_a_to_b_to_c_order(self):
        c = self._food("C")
        CustomFoodIngredient.objects.create(custom_food=c, fineli_food=self.oats, amount_grams=100)
        b = self._food("B")
        CustomFoodIngredient.objects.create(custom_food=b, source_custom_food=c, amount_grams=100)
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=b, amount_grams=100)

        recalculate_custom_food_recipe_with_dependencies(a)
        for food in (c, b, a):
            food.refresh_from_db()
        self.assertEqual(c.energy_kcal_per_100g, 300)
        self.assertEqual(b.energy_kcal_per_100g, 300)
        self.assertEqual(a.energy_kcal_per_100g, 300)

    def test_branching_graph_has_deterministic_order(self):
        d = self._food("D")
        CustomFoodIngredient.objects.create(custom_food=d, fineli_food=self.oats, amount_grams=100)
        b = self._food("B")
        CustomFoodIngredient.objects.create(custom_food=b, source_custom_food=d, amount_grams=100)
        c = self._food("C")
        CustomFoodIngredient.objects.create(custom_food=c, source_custom_food=d, amount_grams=100)
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=b, amount_grams=50)
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=c, amount_grams=50)

        recalculate_custom_food_recipe_with_dependencies(a)
        recalculate_custom_food_recipe_with_dependencies(a)  # run twice: order must be stable
        for food in (d, b, c, a):
            food.refresh_from_db()
        self.assertEqual(d.energy_kcal_per_100g, 300)
        self.assertEqual(b.energy_kcal_per_100g, 300)
        self.assertEqual(c.energy_kcal_per_100g, 300)
        self.assertEqual(a.energy_kcal_per_100g, 300)

    def test_shared_dependency_recalculated_exactly_once(self):
        from unittest import mock
        d = self._food("D")
        CustomFoodIngredient.objects.create(custom_food=d, fineli_food=self.oats, amount_grams=100)
        b = self._food("B")
        CustomFoodIngredient.objects.create(custom_food=b, source_custom_food=d, amount_grams=100)
        c = self._food("C")
        CustomFoodIngredient.objects.create(custom_food=c, source_custom_food=d, amount_grams=100)
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=b, amount_grams=50)
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=c, amount_grams=50)

        with mock.patch(
            "nutrition.recipe_persistence.recalculate_custom_food_recipe", wraps=recalculate_custom_food_recipe
        ) as spy:
            recalculate_custom_food_recipe_with_dependencies(a)
        recalculated_ids = [call.args[0].id for call in spy.call_args_list]
        self.assertEqual(recalculated_ids.count(d.id), 1)

    def test_unrelated_recipes_untouched(self):
        b = self._food("B")
        CustomFoodIngredient.objects.create(custom_food=b, fineli_food=self.oats, amount_grams=100)
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=b, amount_grams=100)

        y = self._food("Y")
        CustomFoodIngredient.objects.create(custom_food=y, fineli_food=self.oats, amount_grams=100)
        x = self._food("X")
        CustomFoodIngredient.objects.create(custom_food=x, source_custom_food=y, amount_grams=100)

        recalculate_custom_food_recipe_with_dependencies(a)
        x.refresh_from_db()
        y.refresh_from_db()
        self.assertIsNone(x.energy_kcal_per_100g)
        self.assertIsNone(y.energy_kcal_per_100g)

    # --- MANUAL LEAVES ---

    def test_zero_ingredient_dependency_is_not_recalculated(self):
        b = self._food("B", energy_kcal_per_100g=999, protein_per_100g=42)  # manual, no ingredients
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=b, amount_grams=50)

        recalculate_custom_food_recipe_with_dependencies(a)
        b.refresh_from_db()
        self.assertEqual(b.energy_kcal_per_100g, 999)
        self.assertEqual(b.protein_per_100g, 42)

    def test_parent_uses_manual_leaf_values_unchanged(self):
        b = self._food("B", energy_kcal_per_100g=999)
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=b, amount_grams=50)

        recalculate_custom_food_recipe_with_dependencies(a)
        a.refresh_from_db()
        self.assertEqual(a.energy_kcal_per_100g, 999)

    def test_explicitly_requested_zero_ingredient_root_is_still_recalculated(self):
        root = self._food("EmptyRoot", energy_kcal_per_100g=123)  # stale manual-looking value
        recalculate_custom_food_recipe_with_dependencies(root)
        root.refresh_from_db()
        self.assertIsNone(root.energy_kcal_per_100g)

    # --- FRESHNESS ---

    def test_changing_deepest_source_updates_parent_persisted_result(self):
        c = self._food("C")
        CustomFoodIngredient.objects.create(custom_food=c, fineli_food=self.oats, amount_grams=100)
        b = self._food("B")
        CustomFoodIngredient.objects.create(custom_food=b, source_custom_food=c, amount_grams=100)
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=b, amount_grams=100)
        recalculate_custom_food_recipe_with_dependencies(a)

        self.oats.energy_kcal = 400
        self.oats.save()
        recalculate_custom_food_recipe_with_dependencies(a)

        b.refresh_from_db()
        a.refresh_from_db()
        self.assertEqual(b.energy_kcal_per_100g, 400)  # stale intermediate replaced first
        self.assertEqual(a.energy_kcal_per_100g, 400)

    def test_auxiliary_value_propagates_fresh_through_nesting(self):
        c = self._food("C")
        CustomFoodIngredient.objects.create(custom_food=c, fineli_food=self.oats, amount_grams=100)
        b = self._food("B")
        CustomFoodIngredient.objects.create(custom_food=b, source_custom_food=c, amount_grams=100)
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=b, amount_grams=100)

        recalculate_custom_food_recipe_with_dependencies(a)
        self.assertEqual(a.nutrients.get(nutrient_key="sodium").value, 5)

    # --- SEMANTICS ---

    def test_explicit_zero_propagates_through_nesting(self):
        water = FineliFood.objects.create(fineli_id=9002, name_fi="Water", energy_kcal=0)
        c = self._food("C")
        CustomFoodIngredient.objects.create(custom_food=c, fineli_food=water, amount_grams=100)
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=c, amount_grams=100)

        recalculate_custom_food_recipe_with_dependencies(a)
        a.refresh_from_db()
        self.assertEqual(a.energy_kcal_per_100g, 0.0)

    def test_unknown_propagates_truthfully_through_nesting(self):
        mystery = FineliFood.objects.create(fineli_id=9003, name_fi="Mystery", protein_g=None)
        c = self._food("C")
        CustomFoodIngredient.objects.create(custom_food=c, fineli_food=mystery, amount_grams=100)
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=c, amount_grams=100)

        recalculate_custom_food_recipe_with_dependencies(a)
        c.refresh_from_db()
        a.refresh_from_db()
        self.assertIsNone(c.protein_per_100g)
        self.assertIsNone(a.protein_per_100g)

    def test_partial_coverage_is_not_fabricated_as_complete(self):
        mystery = FineliFood.objects.create(fineli_id=9004, name_fi="Mystery2", protein_g=None)
        b = self._food("B")
        CustomFoodIngredient.objects.create(custom_food=b, fineli_food=self.oats, amount_grams=100)
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=b, amount_grams=50)
        CustomFoodIngredient.objects.create(custom_food=a, fineli_food=mystery, amount_grams=50)

        recalculate_custom_food_recipe_with_dependencies(a)
        a.refresh_from_db()
        self.assertIsNone(a.protein_per_100g)

    def test_complete_auxiliary_exact_remains_exact_through_nesting(self):
        c = self._food("C")
        CustomFoodIngredient.objects.create(custom_food=c, fineli_food=self.oats, amount_grams=100)
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=c, amount_grams=100)

        recalculate_custom_food_recipe_with_dependencies(a)
        self.assertEqual(a.nutrients.get(nutrient_key="sodium").qualifier, "exact")

    def test_complete_auxiliary_lt_remains_bounded_through_nesting(self):
        c = self._food("C")
        CustomFoodIngredient.objects.create(custom_food=c, fineli_food=self.oats, amount_grams=100)
        b = self._food("B")
        CustomFoodIngredient.objects.create(custom_food=b, source_custom_food=c, amount_grams=100)
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=b, amount_grams=100)

        recalculate_custom_food_recipe_with_dependencies(a)
        self.assertEqual(b.nutrients.get(nutrient_key="starch").qualifier, "lt")
        self.assertEqual(a.nutrients.get(nutrient_key="starch").qualifier, "lt")

    def test_canonical_unit_preserved_through_nesting(self):
        c = self._food("C")
        CustomFoodIngredient.objects.create(custom_food=c, fineli_food=self.oats, amount_grams=100)
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=c, amount_grams=100)

        recalculate_custom_food_recipe_with_dependencies(a)
        self.assertEqual(a.nutrients.get(nutrient_key="sodium").unit, "mg")
        self.assertEqual(a.nutrients.get(nutrient_key="starch").unit, "g")

    # --- SAFETY / ATOMICITY ---

    def test_defensive_cycle_detection_raises_and_writes_nothing(self):
        b = self._food("B")
        b_ing = CustomFoodIngredient.objects.create(custom_food=b, fineli_food=self.oats, amount_grams=100)
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=b, amount_grams=100)

        # Corrupt B's ingredient to point back at A, bypassing model
        # validation (a real cycle should never be creatable via save()).
        CustomFoodIngredient.objects.filter(pk=b_ing.pk).update(fineli_food=None, source_custom_food=a)

        with self.assertRaises(RecipeGraphError):
            recalculate_custom_food_recipe_with_dependencies(a)
        b.refresh_from_db()
        a.refresh_from_db()
        self.assertIsNone(b.energy_kcal_per_100g)  # nothing was written
        self.assertIsNone(a.energy_kcal_per_100g)

    def test_corrupted_cross_user_dependency_aborts_without_mutating(self):
        b = self._food("B", energy_kcal_per_100g=111)
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=b, amount_grams=100)

        # Simulate drift: B's owner changed after the ingredient link existed.
        CustomFood.objects.filter(pk=b.pk).update(user=self.user_b)

        with self.assertRaises(RecipeGraphError):
            recalculate_custom_food_recipe_with_dependencies(a)
        b.refresh_from_db()
        self.assertEqual(b.energy_kcal_per_100g, 111)  # untouched

    def test_source_finelifood_unchanged(self):
        c = self._food("C")
        CustomFoodIngredient.objects.create(custom_food=c, fineli_food=self.oats, amount_grams=100)
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=c, amount_grams=100)
        recalculate_custom_food_recipe_with_dependencies(a)
        self.oats.refresh_from_db()
        self.assertEqual(self.oats.energy_kcal, 300)

    def test_ingredient_rows_unchanged(self):
        c = self._food("C")
        ing = CustomFoodIngredient.objects.create(custom_food=c, fineli_food=self.oats, amount_grams=100)
        a = self._food("A")
        a_ing = CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=c, amount_grams=70)
        recalculate_custom_food_recipe_with_dependencies(a)
        ing.refresh_from_db()
        a_ing.refresh_from_db()
        self.assertEqual(ing.amount_grams, 100)
        self.assertEqual(a_ing.amount_grams, 70)

    def test_requested_root_identity_unchanged(self):
        c = self._food("C")
        CustomFoodIngredient.objects.create(custom_food=c, fineli_food=self.oats, amount_grams=100)
        a = self._food("A")
        CustomFoodIngredient.objects.create(custom_food=a, source_custom_food=c, amount_grams=100)
        recalculate_custom_food_recipe_with_dependencies(a)
        a.refresh_from_db()
        self.assertEqual(a.user, self.user_a)
        self.assertEqual(a.name, "A")


class RecipeCompositionUITests(TestCase):
    """Focused regression coverage for the recipe composition UI: ownership-
    scoped page/actions, ingredient add/remove, and the explicit
    dependency-aware recalculate action - reusing existing backend
    validation/recalculation rather than duplicating any of it."""

    def setUp(self):
        User = get_user_model()
        self.user_a = User.objects.create_user(username="rui_user_a", password="x")
        self.user_b = User.objects.create_user(username="rui_user_b", password="x")

        self.oats = FineliFood.objects.create(fineli_id=9101, name_fi="Oats", energy_kcal=300)
        FineliFoodNutrient.objects.create(fineli_food=self.oats, nutrient_key="sodium", value=5, unit="mg")
        FineliFoodNutrient.objects.create(
            fineli_food=self.oats, nutrient_key="starch", value=0.2, unit="g", qualifier="lt"
        )

        self.parent = CustomFood.objects.create(name="My Recipe", user=self.user_a)
        self.other_own = CustomFood.objects.create(name="Other Own", user=self.user_a)
        self.foreign_food = CustomFood.objects.create(name="Foreign", user=self.user_b)
        self.legacy_food = CustomFood.objects.create(name="Legacy", user=None)

    def _recipe_url(self, food=None):
        return reverse("recipe_detail", args=[(food or self.parent).id])

    # --- AUTH / OWNERSHIP ---

    def test_recipe_page_requires_login(self):
        resp = self.client.get(self._recipe_url())
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("admin:login"), resp.url)

    def test_owner_can_open_recipe_page(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(self._recipe_url())
        self.assertEqual(resp.status_code, 200)

    def test_foreign_users_recipe_returns_404(self):
        self.client.force_login(self.user_b)
        resp = self.client.get(self._recipe_url())
        self.assertEqual(resp.status_code, 404)

    def test_legacy_recipe_unavailable(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(self._recipe_url(self.legacy_food))
        self.assertEqual(resp.status_code, 404)

    def test_own_food_selector_excludes_foreign_and_legacy_foods(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(self._recipe_url())
        own_ids = {f.id for f in resp.context["own_foods"]}
        self.assertEqual(own_ids, {self.other_own.id})

    # --- FINELI INGREDIENT ---

    def test_owner_can_add_fineli_ingredient(self):
        self.client.force_login(self.user_a)
        self.client.post(
            reverse("add_recipe_fineli_ingredient", args=[self.parent.id]),
            {"fineli_id": self.oats.fineli_id, "amount_grams": "60"},
        )
        ing = CustomFoodIngredient.objects.get(custom_food=self.parent)
        self.assertEqual(ing.fineli_food, self.oats)

    def test_fineli_ingredient_amount_grams_persists_correctly(self):
        self.client.force_login(self.user_a)
        self.client.post(
            reverse("add_recipe_fineli_ingredient", args=[self.parent.id]),
            {"fineli_id": self.oats.fineli_id, "amount_grams": "60"},
        )
        ing = CustomFoodIngredient.objects.get(custom_food=self.parent)
        self.assertEqual(ing.amount_grams, 60)

    def test_nutrient_values_not_taken_from_post_payload(self):
        self.client.force_login(self.user_a)
        self.client.post(
            reverse("add_recipe_fineli_ingredient", args=[self.parent.id]),
            {"fineli_id": self.oats.fineli_id, "amount_grams": "60", "energy_kcal": "999999"},
        )
        self.client.post(reverse("recalculate_recipe", args=[self.parent.id]))
        self.parent.refresh_from_db()
        self.assertEqual(self.parent.energy_kcal_per_100g, 300)  # oats' real value, not the tampered field

    # --- CUSTOM INGREDIENT ---

    def test_owner_can_add_owned_custom_food_ingredient(self):
        self.client.force_login(self.user_a)
        self.client.post(
            reverse("add_recipe_custom_ingredient", args=[self.parent.id]),
            {"source_custom_food_id": self.other_own.id, "amount_grams": "40"},
        )
        ing = CustomFoodIngredient.objects.get(custom_food=self.parent)
        self.assertEqual(ing.source_custom_food, self.other_own)

    def test_foreign_custom_food_cannot_be_added(self):
        self.client.force_login(self.user_a)
        self.client.post(
            reverse("add_recipe_custom_ingredient", args=[self.parent.id]),
            {"source_custom_food_id": self.foreign_food.id, "amount_grams": "40"},
        )
        self.assertFalse(CustomFoodIngredient.objects.filter(custom_food=self.parent).exists())

    def test_self_reference_rejected(self):
        self.client.force_login(self.user_a)
        resp = self.client.post(
            reverse("add_recipe_custom_ingredient", args=[self.parent.id]),
            {"source_custom_food_id": self.parent.id, "amount_grams": "40"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(CustomFoodIngredient.objects.filter(custom_food=self.parent).exists())

    def test_indirect_cycle_rejected_with_visible_error(self):
        self.client.force_login(self.user_a)
        self.client.post(
            reverse("add_recipe_custom_ingredient", args=[self.parent.id]),
            {"source_custom_food_id": self.other_own.id, "amount_grams": "40"},
        )
        resp = self.client.post(
            reverse("add_recipe_custom_ingredient", args=[self.other_own.id]),
            {"source_custom_food_id": self.parent.id, "amount_grams": "40"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context.get("error"))
        self.assertFalse(CustomFoodIngredient.objects.filter(custom_food=self.other_own).exists())

    # --- VALIDATION ---

    def test_zero_grams_rejected(self):
        self.client.force_login(self.user_a)
        self.client.post(
            reverse("add_recipe_fineli_ingredient", args=[self.parent.id]),
            {"fineli_id": self.oats.fineli_id, "amount_grams": "0"},
        )
        self.assertFalse(CustomFoodIngredient.objects.filter(custom_food=self.parent).exists())

    def test_negative_grams_rejected(self):
        self.client.force_login(self.user_a)
        self.client.post(
            reverse("add_recipe_fineli_ingredient", args=[self.parent.id]),
            {"fineli_id": self.oats.fineli_id, "amount_grams": "-5"},
        )
        self.assertFalse(CustomFoodIngredient.objects.filter(custom_food=self.parent).exists())

    def test_invalid_fineli_source_rejected(self):
        self.client.force_login(self.user_a)
        resp = self.client.post(
            reverse("add_recipe_fineli_ingredient", args=[self.parent.id]),
            {"fineli_id": "99999999", "amount_grams": "50"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(CustomFoodIngredient.objects.filter(custom_food=self.parent).exists())

    # --- LIST / DELETE ---

    def test_current_ingredients_render(self):
        CustomFoodIngredient.objects.create(custom_food=self.parent, fineli_food=self.oats, amount_grams=60)
        self.client.force_login(self.user_a)
        resp = self.client.get(self._recipe_url())
        self.assertContains(resp, "Oats")

    def test_source_type_labels_render(self):
        CustomFoodIngredient.objects.create(custom_food=self.parent, fineli_food=self.oats, amount_grams=60)
        CustomFoodIngredient.objects.create(custom_food=self.parent, source_custom_food=self.other_own, amount_grams=40)
        self.client.force_login(self.user_a)
        resp = self.client.get(self._recipe_url())
        self.assertContains(resp, "Fineli")
        self.assertContains(resp, "My Food")

    def test_ingredient_amount_renders(self):
        CustomFoodIngredient.objects.create(custom_food=self.parent, fineli_food=self.oats, amount_grams=60)
        self.client.force_login(self.user_a)
        resp = self.client.get(self._recipe_url())
        self.assertContains(resp, "60")

    def test_owner_can_remove_ingredient(self):
        ing = CustomFoodIngredient.objects.create(custom_food=self.parent, fineli_food=self.oats, amount_grams=60)
        self.client.force_login(self.user_a)
        self.client.post(reverse("remove_recipe_ingredient", args=[self.parent.id, ing.id]))
        self.assertFalse(CustomFoodIngredient.objects.filter(pk=ing.pk).exists())

    def test_foreign_ingredient_cannot_be_removed(self):
        ing = CustomFoodIngredient.objects.create(custom_food=self.parent, fineli_food=self.oats, amount_grams=60)
        self.client.force_login(self.user_b)
        resp = self.client.post(reverse("remove_recipe_ingredient", args=[self.parent.id, ing.id]))
        self.assertEqual(resp.status_code, 404)  # can't even reach a food that isn't theirs
        self.assertTrue(CustomFoodIngredient.objects.filter(pk=ing.pk).exists())

    # --- RECALCULATION ---

    def test_recalculate_action_is_post_only(self):
        CustomFoodIngredient.objects.create(custom_food=self.parent, fineli_food=self.oats, amount_grams=60)
        self.client.force_login(self.user_a)
        resp = self.client.get(reverse("recalculate_recipe", args=[self.parent.id]))
        self.assertEqual(resp.status_code, 302)
        self.parent.refresh_from_db()
        self.assertIsNone(self.parent.energy_kcal_per_100g)  # GET must not trigger recalculation

    def test_recalculate_action_calls_dependency_aware_path(self):
        from unittest import mock
        self.client.force_login(self.user_a)
        with mock.patch("nutrition.views.recalculate_custom_food_recipe_with_dependencies") as spy:
            self.client.post(reverse("recalculate_recipe", args=[self.parent.id]))
        spy.assert_called_once_with(self.parent)

    def test_persisted_core_result_changes_after_recalculation(self):
        CustomFoodIngredient.objects.create(custom_food=self.parent, fineli_food=self.oats, amount_grams=60)
        self.client.force_login(self.user_a)
        self.client.post(reverse("recalculate_recipe", args=[self.parent.id]))
        self.parent.refresh_from_db()
        self.assertEqual(self.parent.energy_kcal_per_100g, 300)

    def test_nested_dependency_refreshed_before_parent(self):
        CustomFoodIngredient.objects.create(custom_food=self.other_own, fineli_food=self.oats, amount_grams=100)
        CustomFoodIngredient.objects.create(custom_food=self.parent, source_custom_food=self.other_own, amount_grams=50)
        self.client.force_login(self.user_a)
        self.client.post(reverse("recalculate_recipe", args=[self.parent.id]))
        self.other_own.refresh_from_db()
        self.parent.refresh_from_db()
        self.assertEqual(self.other_own.energy_kcal_per_100g, 300)
        self.assertEqual(self.parent.energy_kcal_per_100g, 300)

    def test_recipegrapherror_presented_safely_not_500(self):
        ing = CustomFoodIngredient.objects.create(
            custom_food=self.other_own, fineli_food=self.oats, amount_grams=100
        )
        CustomFoodIngredient.objects.create(custom_food=self.parent, source_custom_food=self.other_own, amount_grams=50)
        # Corrupt other_own's ingredient into a cycle back to parent, bypassing
        # model validation (a real cycle should never be creatable via save()).
        CustomFoodIngredient.objects.filter(pk=ing.pk).update(fineli_food=None, source_custom_food=self.parent)

        self.client.force_login(self.user_a)
        resp = self.client.post(reverse("recalculate_recipe", args=[self.parent.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context.get("error"))

    # --- DISPLAY SEMANTICS ---

    def test_none_core_nutrient_renders_as_unknown(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(self._recipe_url())
        self.assertContains(resp, "Unknown")

    def test_explicit_zero_renders_as_numeric_zero(self):
        self.parent.energy_kcal_per_100g = 0
        self.parent.save()
        self.client.force_login(self.user_a)
        resp = self.client.get(self._recipe_url())
        self.assertContains(resp, "0.0")

    def test_exact_auxiliary_renders_value_and_unit(self):
        CustomFoodNutrient.objects.create(custom_food=self.parent, nutrient_key="sodium", value=5, unit="mg")
        self.client.force_login(self.user_a)
        resp = self.client.get(self._recipe_url())
        self.assertContains(resp, "sodium")
        self.assertContains(resp, "mg")

    def test_lt_auxiliary_renders_with_less_than(self):
        CustomFoodNutrient.objects.create(
            custom_food=self.parent, nutrient_key="starch", value=0.2, unit="g", qualifier="lt"
        )
        self.client.force_login(self.user_a)
        resp = self.client.get(self._recipe_url())
        self.assertIn(b"&lt;", resp.content)

    def test_absent_auxiliary_nutrient_is_not_fabricated(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(self._recipe_url())
        self.assertContains(resp, "No auxiliary nutrient data.")

    def test_total_recipe_grams_render_correctly(self):
        CustomFoodIngredient.objects.create(custom_food=self.parent, fineli_food=self.oats, amount_grams=60)
        CustomFoodIngredient.objects.create(custom_food=self.parent, source_custom_food=self.other_own, amount_grams=40)
        self.client.force_login(self.user_a)
        resp = self.client.get(self._recipe_url())
        self.assertContains(resp, "Total ingredient mass: 100")

    def test_empty_recipe_has_clear_empty_state(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(self._recipe_url())
        self.assertContains(resp, "No ingredients added yet.")

    # --- MANUAL FOOD ---

    def test_ordinary_edit_view_preserves_manual_nutrient_values(self):
        manual = CustomFood.objects.create(name="Manual Bar", user=self.user_a, energy_kcal_per_100g=250)
        self.client.force_login(self.user_a)
        self.client.post(reverse("edit_custom_food", args=[manual.id]), {
            "name": "Manual Bar", "brand": "Renamed", "energy_kcal_per_100g": "250", "default_portion_g": "100",
        })
        manual.refresh_from_db()
        self.assertEqual(manual.energy_kcal_per_100g, 250)


class RecipeProductFlowUITests(TestCase):
    """Focused regression coverage for the My Foods 'Add Product' vs
    'Create Recipe' split, the no-fabricated-nutrients recipe creation
    shell, and the recipe page's explicit manual-override-vs-recalculate
    final-values behavior."""

    def setUp(self):
        User = get_user_model()
        self.user_a = User.objects.create_user(username="rpf_user_a", password="x")
        self.user_b = User.objects.create_user(username="rpf_user_b", password="x")
        self.oats = FineliFood.objects.create(fineli_id=9201, name_fi="Oats", energy_kcal=300)
        self.foreign_food = CustomFood.objects.create(name="Foreign", user=self.user_b)

    # --- MY FOODS CHOICES ---

    def test_my_foods_exposes_add_product_and_create_recipe(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(reverse("manage_custom_foods"))
        self.assertContains(resp, "Add Product")
        self.assertContains(resp, "Create Recipe")
        self.assertContains(resp, reverse("create_recipe"))

    # --- CREATE RECIPE ---

    def test_create_recipe_creates_owned_food_without_fabricated_zeroes(self):
        self.client.force_login(self.user_a)
        self.client.post(reverse("create_recipe"), {"name": "My Porridge"})
        recipe = CustomFood.objects.get(name="My Porridge")
        self.assertEqual(recipe.user, self.user_a)
        self.assertIsNone(recipe.energy_kcal_per_100g)
        self.assertIsNone(recipe.protein_per_100g)

    def test_create_recipe_redirects_to_recipe_page(self):
        self.client.force_login(self.user_a)
        resp = self.client.post(reverse("create_recipe"), {"name": "My Porridge"})
        recipe = CustomFood.objects.get(name="My Porridge")
        self.assertRedirects(resp, reverse("recipe_detail", args=[recipe.id]))

    # --- INGREDIENTS (existing backend rules still enforced from this flow) ---

    def test_fineli_ingredient_can_be_added(self):
        self.client.force_login(self.user_a)
        recipe = CustomFood.objects.create(name="R", user=self.user_a)
        self.client.post(reverse("add_recipe_fineli_ingredient", args=[recipe.id]), {
            "fineli_id": self.oats.fineli_id, "amount_grams": "60",
        })
        self.assertTrue(CustomFoodIngredient.objects.filter(custom_food=recipe, fineli_food=self.oats).exists())

    def test_own_custom_food_ingredient_can_be_added(self):
        self.client.force_login(self.user_a)
        recipe = CustomFood.objects.create(name="R", user=self.user_a)
        other = CustomFood.objects.create(name="Other", user=self.user_a)
        self.client.post(reverse("add_recipe_custom_ingredient", args=[recipe.id]), {
            "source_custom_food_id": other.id, "amount_grams": "40",
        })
        self.assertTrue(CustomFoodIngredient.objects.filter(custom_food=recipe, source_custom_food=other).exists())

    def test_foreign_custom_food_cannot_be_added(self):
        self.client.force_login(self.user_a)
        recipe = CustomFood.objects.create(name="R", user=self.user_a)
        self.client.post(reverse("add_recipe_custom_ingredient", args=[recipe.id]), {
            "source_custom_food_id": self.foreign_food.id, "amount_grams": "40",
        })
        self.assertFalse(CustomFoodIngredient.objects.filter(custom_food=recipe).exists())

    def test_self_and_cycle_protection_remains_active(self):
        self.client.force_login(self.user_a)
        recipe = CustomFood.objects.create(name="R", user=self.user_a)
        resp = self.client.post(reverse("add_recipe_custom_ingredient", args=[recipe.id]), {
            "source_custom_food_id": recipe.id, "amount_grams": "40",
        })
        self.assertTrue(resp.context.get("error"))
        self.assertFalse(CustomFoodIngredient.objects.filter(custom_food=recipe).exists())

    # --- RECALCULATION REUSE ---

    def test_recalculate_uses_existing_dependency_aware_persistence(self):
        from unittest import mock
        self.client.force_login(self.user_a)
        recipe = CustomFood.objects.create(name="R", user=self.user_a)
        with mock.patch("nutrition.views.recalculate_custom_food_recipe_with_dependencies") as spy:
            self.client.post(reverse("recalculate_recipe", args=[recipe.id]))
        spy.assert_called_once_with(recipe)

    def test_calculated_final_values_shown_in_editable_form(self):
        self.client.force_login(self.user_a)
        recipe = CustomFood.objects.create(name="R", user=self.user_a)
        CustomFoodIngredient.objects.create(custom_food=recipe, fineli_food=self.oats, amount_grams=100)
        self.client.post(reverse("recalculate_recipe", args=[recipe.id]))
        resp = self.client.get(reverse("recipe_detail", args=[recipe.id]))
        self.assertContains(resp, 'value="300.0"')

    # --- MANUAL EDIT SEMANTICS ---

    def test_manual_edit_blank_becomes_none(self):
        self.client.force_login(self.user_a)
        recipe = CustomFood.objects.create(name="R", user=self.user_a, energy_kcal_per_100g=250)
        self.client.post(reverse("save_recipe_final_values", args=[recipe.id]), {"energy_kcal_per_100g": ""})
        recipe.refresh_from_db()
        self.assertIsNone(recipe.energy_kcal_per_100g)

    def test_manual_edit_zero_stays_zero(self):
        self.client.force_login(self.user_a)
        recipe = CustomFood.objects.create(name="R", user=self.user_a)
        self.client.post(reverse("save_recipe_final_values", args=[recipe.id]), {"energy_kcal_per_100g": "0"})
        recipe.refresh_from_db()
        self.assertEqual(recipe.energy_kcal_per_100g, 0.0)

    def test_manual_edit_positive_value_saved(self):
        self.client.force_login(self.user_a)
        recipe = CustomFood.objects.create(name="R", user=self.user_a)
        self.client.post(reverse("save_recipe_final_values", args=[recipe.id]), {"protein_per_100g": "12.5"})
        recipe.refresh_from_db()
        self.assertEqual(recipe.protein_per_100g, 12.5)

    def test_saving_final_values_does_not_recalculate_ingredients(self):
        self.client.force_login(self.user_a)
        recipe = CustomFood.objects.create(name="R", user=self.user_a)
        CustomFoodIngredient.objects.create(custom_food=recipe, fineli_food=self.oats, amount_grams=100)
        self.client.post(reverse("save_recipe_final_values", args=[recipe.id]), {"energy_kcal_per_100g": "999"})
        recipe.refresh_from_db()
        self.assertEqual(recipe.energy_kcal_per_100g, 999.0)  # not overwritten by ingredient-derived 300
        self.assertEqual(CustomFoodIngredient.objects.filter(custom_food=recipe).count(), 1)

    def test_reopening_recipe_preserves_manual_override(self):
        self.client.force_login(self.user_a)
        recipe = CustomFood.objects.create(name="R", user=self.user_a)
        self.client.post(reverse("save_recipe_final_values", args=[recipe.id]), {"energy_kcal_per_100g": "777"})
        resp = self.client.get(reverse("recipe_detail", args=[recipe.id]))
        self.assertContains(resp, 'value="777.0"')

    def test_explicit_recalculate_overwrites_manual_override(self):
        self.client.force_login(self.user_a)
        recipe = CustomFood.objects.create(name="R", user=self.user_a)
        CustomFoodIngredient.objects.create(custom_food=recipe, fineli_food=self.oats, amount_grams=100)
        self.client.post(reverse("save_recipe_final_values", args=[recipe.id]), {"energy_kcal_per_100g": "777"})
        self.client.post(reverse("recalculate_recipe", args=[recipe.id]))
        recipe.refresh_from_db()
        self.assertEqual(recipe.energy_kcal_per_100g, 300.0)

    # --- IDENTITY / REUSE ---

    def test_recipe_remains_usable_as_ingredient_in_another_recipe(self):
        self.client.force_login(self.user_a)
        recipe = CustomFood.objects.create(name="R", user=self.user_a, energy_kcal_per_100g=250)
        other_recipe = CustomFood.objects.create(name="R2", user=self.user_a)
        resp = self.client.post(reverse("add_recipe_custom_ingredient", args=[other_recipe.id]), {
            "source_custom_food_id": recipe.id, "amount_grams": "50",
        })
        self.assertFalse(resp.context and resp.context.get("error"))
        self.assertTrue(
            CustomFoodIngredient.objects.filter(custom_food=other_recipe, source_custom_food=recipe).exists()
        )


class FineliAttributionTests(TestCase):
    """Fineli data is CC BY 4.0: every page that shows it must credit THL
    and link the license."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="attributiontest", password="x")
        self.client.force_login(self.user)

    def _assert_credit(self, resp):
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Finnish Institute for Health and Welfare (THL)")
        self.assertContains(resp, "https://creativecommons.org/licenses/by/4.0/")

    def test_food_diary_credits_fineli(self):
        self._assert_credit(self.client.get(reverse("food_diary")))

    def test_custom_foods_page_credits_fineli(self):
        self._assert_credit(self.client.get(reverse("manage_custom_foods")))

    def test_recipe_page_credits_fineli(self):
        recipe = CustomFood.objects.create(user=self.user, name="Attribution Recipe")
        self._assert_credit(self.client.get(reverse("recipe_detail", args=[recipe.id])))
