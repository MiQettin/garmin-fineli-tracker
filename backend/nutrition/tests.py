from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from core.models import DailyLog
from .aggregation import daily_nutrient_result
from .models import (
    CustomFood, CustomFoodIngredient, CustomFoodNutrient, FavoriteFood, FineliFood, FineliFoodNutrient, FineliFoodUnit,
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

    def test_overlapping_files_are_deduplicated(self):
        self.client.post(self.url, {
            "csv_files": [upload(diary_csv()), upload(diary_csv())],
        })

        self.assertEqual(
            MealEntry.objects.filter(
                daily_log__date="2026-08-16", source_type="bulk_import"
            ).count(),
            2,
        )

    def test_invalid_file_aborts_the_whole_upload(self):
        response = self.client.post(self.url, {
            "csv_files": [
                upload(diary_csv()),
                upload("not a Fineli CSV"),
            ],
        })

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])
        self.assertFalse(
            MealEntry.objects.filter(source_type="bulk_import").exists()
        )

    def test_existing_duplicate_rows_abort_without_more_duplicates(self):
        self.client.post(self.url, {"csv_files": upload(diary_csv())})
        entry = MealEntry.objects.get(
            daily_log__date="2026-08-16",
            source_type="bulk_import",
            food_name="Test Chicken",
        )
        MealEntry.objects.create(
            daily_log=entry.daily_log,
            user=self.user,
            source_type="bulk_import",
            food_name=entry.food_name,
            meal_type=entry.meal_type,
            portion_grams=entry.portion_grams,
            calories=entry.calories,
            protein_g=entry.protein_g,
            carbs_g=entry.carbs_g,
            fat_g=entry.fat_g,
            sat_fat_g=entry.sat_fat_g,
            sugar_g=entry.sugar_g,
            fiber_g=entry.fiber_g,
            salt_g=entry.salt_g,
            source_food_id=entry.source_food_id,
            source_amount=entry.source_amount,
            source_unit=entry.source_unit,
        )

        response = self.client.post(self.url, {"csv_files": upload(diary_csv())})

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()["success"])
        self.assertEqual(
            MealEntry.objects.filter(
                daily_log__date="2026-08-16", source_type="bulk_import"
            ).count(),
            3,
        )

    def test_corrected_import_preserves_existing_bulk_rows(self):
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
        self.assertEqual(bulk_entries.count(), 3)
        self.assertTrue(bulk_entries.filter(food_name="Corrected Soup").exists())
        self.assertTrue(bulk_entries.filter(food_name="Test Oatmeal").exists())
        self.assertTrue(bulk_entries.filter(food_name="Test Chicken").exists())
        self.assertTrue(MealEntry.objects.filter(pk=manual.pk).exists())

        log.refresh_from_db()
        expected = sum(entry.calories for entry in bulk_entries) + manual.calories
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

    def test_corrected_reimport_preserves_existing_provenance(self):
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
        self.assertEqual(entries.count(), 4)
        replacement = entries.get(food_name="Replacement Meal")
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

    def test_corrected_reimport_preserves_classification(self):
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
        self.assertEqual(entries.count(), 3)
        replacement = entries.get(food_name="Replacement Meal")
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

    def test_corrected_reimport_preserves_existing_nutrient_values(self):
        self.client.post(self.url, {"csv_files": upload(aux_diary_csv())})
        self.client.post(self.url, {"csv_files": upload(corrected_aux_diary_csv())})

        entries = MealEntry.objects.filter(daily_log__date="2026-08-16", source_type="bulk_import")
        self.assertEqual(entries.count(), 3)
        soup = entries.get(food_name="Corrected Soup")

        self.assertEqual(soup.nutrients.get(nutrient_key="starch").value, 99.0)
        self.assertEqual(soup.nutrients.get(nutrient_key="sodium").value, 999.0)
        # Existing rows and their nutrient children remain alongside the correction.
        self.assertEqual(MealEntryNutrient.objects.count(), 10)

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
    """Regression coverage for the quick-add round trip: an unknown (None)
    nutrient must reach MealEntry as None, not a fabricated 0. Values come
    from the stored Fineli food since 2026-10-12, not from the request."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="fineliaddnullzerotest", password="x")
        self.client.force_login(self.user)
        self.url = reverse("add_fineli_entry")

    def _post(self, protein):
        import json as _json
        FineliFood.objects.create(
            fineli_id=999, name_fi="RoundTrip Food", energy_kcal=100, protein_g=protein, carbs_g=10,
            sugar_g=1, fat_g=2, sat_fat_g=1, fiber_g=1, salt_g=0.5,
        )
        payload = {
            "date": "2026-08-21", "fineli_id": 999, "name": "RoundTrip Food",
            "meal_type": "snack", "portion_grams": 100,
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

    def test_login_after_logout_opens_dashboard_not_admin(self):
        self.client.force_login(self.user_a)
        resp = self.client.post(reverse("admin:logout"))
        self.assertEqual(resp.status_code, 302)
        login_url = resp.url
        self.assertTrue(login_url.startswith("/admin/login/"))
        resp = self.client.post(login_url, {"username": "alr_user_a", "password": "x"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("week_view"))

    def test_admin_site_still_reachable_directly(self):
        self.user_a.is_superuser = True
        self.user_a.save()
        self.client.force_login(self.user_a)
        resp = self.client.get("/admin/")
        self.assertEqual(resp.status_code, 200)


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

    def test_day_summary_totals_only_own_entries(self):
        # The shared DailyLog sums everyone's rows (300 + 400 + 500); the
        # diary summary must show only the viewing user's intake.
        self.client.force_login(self.user_a)
        resp = self.client.get(reverse("food_diary"), {"date": self.DATE})
        self.assertEqual(resp.context["day_totals"]["calories_in"], 300)
        self.client.force_login(self.user_b)
        resp = self.client.get(reverse("food_diary"), {"date": self.DATE})
        self.assertEqual(resp.context["day_totals"]["calories_in"], 400)

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
        resp = self.client.post(reverse("add_fineli_entry"), data=_json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(MealEntry.objects.filter(daily_log__date="2026-09-01").exists())

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

    def _post_rejected(self, food_id, date):
        import json as _json
        resp = self.client.post(reverse("add_fineli_entry"), data=_json.dumps({
            "date": date, "source": "custom", "custom_food_id": food_id,
            "name": "Not mine", "meal_type": "snack", "portion_grams": 100,
        }), content_type="application/json")
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(MealEntry.objects.filter(daily_log__date=date).exists())

    def test_user_a_cannot_quick_add_user_b_custom_food(self):
        self.client.force_login(self.user_a)
        self._post_rejected(self.food_b.id, "2026-09-21")

    def test_legacy_custom_food_cannot_be_quick_added(self):
        self.client.force_login(self.user_a)
        self._post_rejected(self.legacy_food.id, "2026-09-22")

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
        FineliFoodUnit.objects.create(fineli_food=self.oats, unit_code="DL", mass_g=80)
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

    def test_fineli_ingredient_portion_unit_is_converted_to_grams(self):
        self.client.force_login(self.user_a)
        self.client.post(
            reverse("add_recipe_fineli_ingredient", args=[self.parent.id]),
            {"fineli_id": self.oats.fineli_id, "amount": "1.25", "unit": "DL"},
        )
        ing = CustomFoodIngredient.objects.get(custom_food=self.parent)
        self.assertEqual(ing.amount_grams, 100)

    def test_fineli_ingredient_unit_must_belong_to_food(self):
        self.client.force_login(self.user_a)
        response = self.client.post(
            reverse("add_recipe_fineli_ingredient", args=[self.parent.id]),
            {"fineli_id": self.oats.fineli_id, "amount": "1", "unit": "KPL_L"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomFoodIngredient.objects.filter(custom_food=self.parent).exists())

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

    def test_fineli_search_renders_food_portion_units(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(self._recipe_url(), {"q": "Oats"})
        # The result row carries its units; the amount panel builds the
        # unit <select name="unit"> from them when the food is chosen.
        self.assertContains(resp, 'name="unit"')
        self.assertContains(resp, 'data-units="DL|dl|')

    def test_auxiliary_nutrient_labels_follow_app_language(self):
        CustomFoodNutrient.objects.create(
            custom_food=self.parent, nutrient_key="calcium", value=79.216, unit="mg"
        )
        CustomFoodIngredient.objects.create(
            custom_food=self.parent, fineli_food=self.oats, amount_grams=100
        )
        self.client.force_login(self.user_a)
        response = self.client.get(self._recipe_url())
        self.assertContains(response, "Calcium")
        self.assertContains(response, "79.22 mg")

        from core.models import UserSettings
        UserSettings.objects.update_or_create(pk=1, defaults={"language": "fi"})
        response = self.client.get(self._recipe_url())
        self.assertContains(response, "Kalsium")

    def test_final_nutrition_values_are_displayed_to_two_decimals(self):
        self.parent.energy_kcal_per_100g = 256.47832364303275
        self.parent.protein_per_100g = 10.190530612244899
        self.parent.save()
        self.client.force_login(self.user_a)
        response = self.client.get(self._recipe_url())
        self.assertContains(response, 'value="256.48"')
        self.assertContains(response, 'value="10.19"')

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
        self.assertContains(resp, "Sodium")
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
        self.assertContains(resp, "No other nutrient data.")

    def test_total_recipe_grams_render_correctly(self):
        CustomFoodIngredient.objects.create(custom_food=self.parent, fineli_food=self.oats, amount_grams=60)
        CustomFoodIngredient.objects.create(custom_food=self.parent, source_custom_food=self.other_own, amount_grams=40)
        self.client.force_login(self.user_a)
        resp = self.client.get(self._recipe_url())
        self.assertContains(resp, '<span class="ing-name">Total</span>')
        self.assertContains(resp, "<b>100 g</b>")

    def test_empty_recipe_has_clear_empty_state(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(self._recipe_url())
        self.assertContains(resp, "No ingredients yet - add them below.")

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

    def test_my_foods_exposes_add_product_and_recipes_link(self):
        # Recipes have their own page (with Create Recipe) since 2026-10-12.
        self.client.force_login(self.user_a)
        resp = self.client.get(reverse("manage_custom_foods"))
        self.assertContains(resp, "Add Product")
        self.assertContains(resp, reverse("recipe_list"))

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
        self.assertContains(resp, 'value="300.00"')

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
        self.assertContains(resp, 'value="777.00"')

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


class FineliPortionUnitTests(TestCase):
    """Fineli portion sizes (foodaddunit.csv -> FineliFoodUnit) offered in
    the Add Food dialog, with grams computed server-side from the unit."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="portionunittest", password="x")
        self.client.force_login(self.user)
        self.banana = FineliFood.objects.create(
            fineli_id=11049, name_fi="BANAANI, KUORITTU", energy_kcal=100, protein_g=1.0,
        )
        for code, grams in [("PORTM", 125), ("KPL_L", 150), ("KPL_S", 100), ("DL", 90)]:
            FineliFoodUnit.objects.create(fineli_food=self.banana, unit_code=code, mass_g=grams)

    def _add(self, **extra):
        import json as _json
        payload = {
            "date": "2026-10-11", "fineli_id": 11049, "name": "BANAANI, KUORITTU",
            "meal_type": "snack", "energy_kcal": 100, "protein": 1.0, "portion_grams": 999,
        }
        payload.update(extra)
        return self.client.post(reverse("add_fineli_entry"), data=_json.dumps(payload), content_type="application/json")

    # --- import ---

    def _run_import(self, unit_rows, with_unit_file=True):
        import io
        import os
        import tempfile
        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as data_folder:
            with open(os.path.join(data_folder, "foodname_FI.csv"), "w", encoding="latin-1") as f:
                f.write("FOODID;FOODNAME\n7;Food 7\n")
            with open(os.path.join(data_folder, "component_value.csv"), "w", encoding="utf-8-sig") as f:
                f.write("FOODID;EUFDNAME;BESTLOC\n7;ENERC;418,4\n")
            if with_unit_file:
                with open(os.path.join(data_folder, "foodaddunit.csv"), "w", encoding="utf-8") as f:
                    f.write("FOODID;FOODUNIT;MASS\n")
                    for code, mass in unit_rows:
                        f.write(f"7;{code};{mass}\n")
            call_command("import_fineli_csv", data_folder=data_folder, stdout=io.StringIO())
        return {u.unit_code: u.mass_g for u in FineliFood.objects.get(fineli_id=7).units.all()}

    def test_import_reads_units_with_decimal_comma(self):
        self.assertEqual(self._run_import([("KPL_M", "125,50"), ("PORTS", "100,00")]), {"KPL_M": 125.5, "PORTS": 100.0})

    def test_reimport_replaces_units(self):
        self._run_import([("KPL_M", "125,00"), ("DL", "90,00")])
        self.assertEqual(self._run_import([("KPL_M", "130,00")]), {"KPL_M": 130.0})

    def test_import_without_unit_file_still_imports_food(self):
        self.assertEqual(self._run_import([], with_unit_file=False), {})
        self.assertTrue(FineliFood.objects.filter(fineli_id=7).exists())

    # --- search API ---

    def test_search_returns_units_in_picker_order(self):
        resp = self.client.get(reverse("search_fineli_api"), {"q": "banaani"})
        units = resp.json()["results"][0]["units"]
        self.assertEqual([u["code"] for u in units], ["KPL_S", "KPL_L", "PORTM", "DL"])
        self.assertEqual(units[1], {"code": "KPL_L", "label": "large piece", "grams": 150.0})

    # --- adding an entry ---

    def test_unit_portion_grams_computed_server_side(self):
        self._add(amount=2, unit="KPL_L")
        entry = MealEntry.objects.get(food_name="BANAANI, KUORITTU")
        self.assertEqual(entry.portion_grams, 300)  # 2 x 150 g, client's 999 ignored
        self.assertEqual(entry.calories, 300)
        self.assertEqual((entry.source_amount, entry.source_unit), (2, "KPL_L"))
        self.assertEqual(entry.portion_label, "2 × large piece")

    def test_gram_unit_uses_amount_as_grams(self):
        self._add(amount=80, unit="G")
        entry = MealEntry.objects.get(food_name="BANAANI, KUORITTU")
        self.assertEqual(entry.portion_grams, 80)
        self.assertEqual(entry.portion_label, "")

    def test_unknown_unit_rejected_without_side_effects(self):
        resp = self._add(amount=1, unit="KPL_M")  # banana has no KPL_M row here
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(MealEntry.objects.exists())
        self.banana.refresh_from_db()
        self.assertEqual(self.banana.usage_count, 0)

    def test_invalid_amount_rejected(self):
        self.assertEqual(self._add(amount=0, unit="KPL_L").status_code, 400)
        self.assertEqual(self._add(amount="x", unit="KPL_L").status_code, 400)
        self.assertFalse(MealEntry.objects.exists())

    def test_without_unit_portion_grams_still_used(self):
        self._add(portion_grams=50)
        entry = MealEntry.objects.get(food_name="BANAANI, KUORITTU")
        self.assertEqual(entry.portion_grams, 50)
        self.assertEqual(entry.source_unit, "")

    def test_custom_food_is_not_treated_as_fineli_id(self):
        import json as _json
        food = CustomFood.objects.create(user=self.user, name="Aamupuuro", energy_kcal_per_100g=80)
        before = FineliFood.objects.count()
        self.client.post(reverse("add_fineli_entry"), data=_json.dumps({
            "date": "2026-10-11", "source": "custom", "custom_food_id": food.id, "name": "Aamupuuro",
            "meal_type": "breakfast", "portion_grams": 200, "energy_kcal": 80,
        }), content_type="application/json")
        entry = MealEntry.objects.get(food_name="Aamupuuro")
        self.assertEqual(entry.custom_food, food)
        self.assertIsNone(entry.fineli_food)
        self.assertEqual(FineliFood.objects.count(), before)

    # --- display and editing ---

    def test_diary_shows_portion_label(self):
        self._add(amount=1, unit="KPL_L")
        resp = self.client.get(reverse("food_diary"), {"date": "2026-10-11"})
        self.assertContains(resp, "1 × large piece · 150g")

    def test_csv_imported_portion_also_labelled(self):
        log, _ = DailyLog.objects.get_or_create(date="2026-10-11")
        entry = MealEntry.objects.create(
            daily_log=log, user=self.user, source_type="bulk_import", food_name="X",
            portion_grams=50, source_amount=0.5, source_unit="DL",
        )
        self.assertEqual(entry.portion_label, "0.5 × dl")

    def test_editing_grams_clears_portion(self):
        self._add(amount=1, unit="KPL_L")
        entry = MealEntry.objects.get(food_name="BANAANI, KUORITTU")
        self.client.post(reverse("edit_meal_entry", args=[entry.id]), {
            "food_name": entry.food_name, "meal_type": "snack", "portion_grams": "120",
        })
        entry.refresh_from_db()
        self.assertEqual(entry.portion_grams, 120)
        self.assertEqual((entry.source_amount, entry.source_unit), (None, ""))

    def test_editing_other_fields_keeps_portion(self):
        self._add(amount=1, unit="KPL_L")
        entry = MealEntry.objects.get(food_name="BANAANI, KUORITTU")
        self.client.post(reverse("edit_meal_entry", args=[entry.id]), {
            "food_name": "Banaani", "meal_type": "snack", "portion_grams": "150",
        })
        entry.refresh_from_db()
        self.assertEqual((entry.source_amount, entry.source_unit), (1, "KPL_L"))


class FoodDiaryUnknownNutrientTests(TestCase):
    """The diary page must render when an entry has an unknown (None)
    nutrient - some Fineli foods lack e.g. sugar or fiber."""

    def test_diary_renders_with_unknown_nutrients(self):
        user = get_user_model().objects.create_user(username="unknownnutrientpage", password="x")
        self.client.force_login(user)
        log, _ = DailyLog.objects.get_or_create(date="2026-10-11")
        MealEntry.objects.create(
            daily_log=log, user=user, source_type="manual", food_name="Partial", meal_type="lunch",
            portion_grams=100, calories=200, protein_g=10, carbs_g=None, sugar_g=None, fat_g=5,
        )
        resp = self.client.get(reverse("food_diary"), {"date": "2026-10-11"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Partial")


class CustomProductPackageTests(TestCase):
    """Custom products can be logged as "1 × pakkaus" (default_portion_g
    grams); recipes stay grams-only."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="packagetest", password="x")
        self.client.force_login(self.user)
        self.rahka = CustomFood.objects.create(
            user=self.user, name="Proteiinirahka", energy_kcal_per_100g=60, protein_per_100g=10,
            default_portion_g=200,
        )

    def _add(self, food, **extra):
        import json as _json
        payload = {
            "date": "2026-10-11", "source": "custom", "custom_food_id": food.id, "name": food.name,
            "meal_type": "snack", "energy_kcal": food.energy_kcal_per_100g, "protein": food.protein_per_100g,
            "portion_grams": 999,
        }
        payload.update(extra)
        return self.client.post(reverse("add_fineli_entry"), data=_json.dumps(payload), content_type="application/json")

    def test_one_package_uses_package_size(self):
        self._add(self.rahka, amount=1, unit="PKG")
        entry = MealEntry.objects.get(food_name="Proteiinirahka")
        self.assertEqual(entry.portion_grams, 200)
        self.assertEqual(entry.calories, 120)
        self.assertEqual(entry.protein_g, 20)
        self.assertEqual(entry.portion_label, "1 × package")

    def test_half_package(self):
        self._add(self.rahka, amount=0.5, unit="PKG")
        self.assertEqual(MealEntry.objects.get(food_name="Proteiinirahka").portion_grams, 100)

    def test_liquid_package_uses_millilitres_for_per_100ml_nutrition(self):
        milk = CustomFood.objects.create(
            user=self.user, name="Maito", energy_kcal_per_100g=46.25,
            protein_per_100g=3.40, package_amount=1, package_unit="L",
            nutrition_basis="ML",
        )
        self._add(milk, amount=2, unit="DL")
        entry = MealEntry.objects.get(food_name="Maito")
        self.assertEqual(entry.portion_grams, 200)
        self.assertAlmostEqual(entry.calories, 92.5)
        self.assertAlmostEqual(entry.protein_g, 6.8)
        self.assertEqual(entry.portion_label, "2 × dl")

    def test_grams_for_custom_food(self):
        self._add(self.rahka, amount=150, unit="G")
        entry = MealEntry.objects.get(food_name="Proteiinirahka")
        self.assertEqual(entry.portion_grams, 150)
        self.assertEqual(entry.portion_label, "")

    def test_unknown_unit_rejected_without_side_effects(self):
        self.assertEqual(self._add(self.rahka, amount=1, unit="KPL_M").status_code, 400)
        self.assertFalse(MealEntry.objects.exists())
        self.rahka.refresh_from_db()
        self.assertEqual(self.rahka.use_count, 0)

    def test_food_list_marks_recipes(self):
        recipe = CustomFood.objects.create(user=self.user, name="Lettutaikina")
        oats = FineliFood.objects.create(fineli_id=153, name_fi="KAURAHIUTALE", energy_kcal=380)
        CustomFoodIngredient.objects.create(custom_food=recipe, fineli_food=oats, amount_grams=100)
        flags = {f["name"]: f["is_recipe"] for f in self.client.get(reverse("list_custom_foods_api")).json()["results"]}
        self.assertEqual(flags, {"Proteiinirahka": False, "Lettutaikina": True})


class EditCustomFoodPageTests(TestCase):
    """The edit page must show and save the real CustomFoodForm fields (it
    used to render nonexistent fields, so saving silently failed)."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="editcustomfood", password="x")
        self.client.force_login(self.user)
        self.food = CustomFood.objects.create(
            user=self.user, name="Rahka", energy_kcal_per_100g=60, protein_per_100g=10, default_portion_g=200,
        )
        self.url = reverse("edit_custom_food", args=[self.food.id])

    def test_page_shows_current_values(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, 'name="energy_kcal_per_100g"')
        self.assertContains(resp, 'name="default_portion_g"')
        self.assertContains(resp, 'value="200.0"')
        self.assertContains(resp, 'step="0.01"')

    def test_save_updates_values(self):
        resp = self.client.post(self.url, {
            "name": "Rahka", "brand": "", "energy_kcal_per_100g": "65", "protein_per_100g": "11",
            "default_portion_g": "250",
        })
        self.assertRedirects(resp, reverse("manage_custom_foods"))
        self.food.refresh_from_db()
        self.assertEqual((self.food.energy_kcal_per_100g, self.food.protein_per_100g, self.food.default_portion_g), (65, 11, 250))

    def test_invalid_save_shows_error(self):
        resp = self.client.post(self.url, {
            "name": "Rahka", "energy_kcal_per_100g": "65", "package_amount": "2", "package_unit": "DL",
            "nutrition_basis": "G",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "errorlist")

    def test_package_size_can_be_cleared(self):
        self.food.package_amount = 200
        self.food.save()
        resp = self.client.post(self.url, {
            "name": "Rahka", "energy_kcal_per_100g": "60", "package_amount": "", "package_unit": "G",
            "nutrition_basis": "G", "default_portion_g": "200",
        })
        self.assertRedirects(resp, reverse("manage_custom_foods"))
        self.food.refresh_from_db()
        self.assertIsNone(self.food.package_amount)


class OptionalPackageSizeTests(TestCase):
    """Package size is optional - e.g. syrup is measured, not eaten by the
    package. Without one the product is logged by weight or volume."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="optionalpackage", password="x")
        self.client.force_login(self.user)

    def _create(self, **fields):
        data = {"name": "Vaahterasiirappi", "brand": "Änglamark", "energy_kcal_per_100g": "270",
                "carbs_per_100g": "67", "sugar_per_100g": "66", "salt_per_100g": "0.03",
                "package_amount": "", "package_unit": "G", "nutrition_basis": "G",
                # The browser always posts the hidden field, empty for a new product.
                "default_portion_g": ""}
        data.update(fields)
        return self.client.post(reverse("manage_custom_foods"), data)

    def test_product_without_package_size_saves(self):
        resp = self._create()
        self.assertRedirects(resp, reverse("manage_custom_foods"))
        food = CustomFood.objects.get(name="Vaahterasiirappi")
        self.assertIsNone(food.package_amount)
        self.assertEqual(food.energy_kcal_per_100g, 270)

    def test_per_100ml_product_without_package_size_saves(self):
        self._create(nutrition_basis="ML", package_unit="ML")
        food = CustomFood.objects.get(name="Vaahterasiirappi")
        self.assertIsNone(food.package_amount)
        self.assertEqual(food.nutrition_basis, "ML")

    def test_package_unit_ignored_when_no_package(self):
        # A leftover "ml" selector with no size must not trip the
        # per-100 g basis/unit check.
        self._create(package_unit="ML")
        self.assertTrue(CustomFood.objects.filter(name="Vaahterasiirappi").exists())

    def test_basis_unit_mismatch_still_rejected_with_package(self):
        resp = self._create(package_amount="189", package_unit="ML", nutrition_basis="G")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "give the package size in grams")
        self.assertFalse(CustomFood.objects.filter(name="Vaahterasiirappi").exists())

    def test_legacy_add_endpoint_shows_errors_instead_of_crashing(self):
        resp = self.client.post(reverse("add_custom_food"), {
            "name": "X", "energy_kcal_per_100g": "1", "package_amount": "189",
            "package_unit": "ML", "nutrition_basis": "G",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "give the package size in grams")

    def test_no_package_product_logged_by_volume(self):
        import json as _json
        self._create(nutrition_basis="ML", package_unit="ML")
        food = CustomFood.objects.get(name="Vaahterasiirappi")
        resp = self.client.post(reverse("add_fineli_entry"), data=_json.dumps({
            "date": "2026-10-11", "source": "custom", "custom_food_id": food.id, "name": food.name,
            "meal_type": "breakfast", "amount": 15, "unit": "ML", "portion_grams": 15,
        }), content_type="application/json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json().get("success"), resp.json())
        entry = MealEntry.objects.get(custom_food=food)
        self.assertEqual(entry.portion_grams, 15)
        self.assertEqual(entry.portion_label, "15 × ml")

    def test_api_reports_no_package(self):
        self._create()
        foods = self.client.get(reverse("list_custom_foods_api")).json()["results"]
        self.assertIsNone(foods[0]["package_amount"])

    def test_form_label_says_optional(self):
        resp = self.client.get(reverse("manage_custom_foods"))
        self.assertContains(resp, "Package size (optional)")


class NonAsciiNameSearchTests(TestCase):
    """SQLite's LIKE folds case for ASCII only - "vehnäjauho" must still find
    Fineli's upper-case "VEHNÄJAUHO", in every food search."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="nonasciisearch", password="x")
        self.client.force_login(self.user)
        self.flour = FineliFood.objects.create(fineli_id=34249, name_fi="JAUHO, ERIKOISVEHNÄJAUHO, VÄHITTÄISKAUPPA", energy_kcal=350)
        self.recipe = CustomFood.objects.create(user=self.user, name="Pannukakku")

    def test_recipe_page_search_matches_lowercase_umlauts(self):
        resp = self.client.get(reverse("recipe_detail", args=[self.recipe.id]), {"q": "vehnäjauho"})
        self.assertEqual(list(resp.context["fineli_results"]), [self.flour])

    def test_diary_search_api_matches_lowercase_umlauts(self):
        for query in ("vehnäjauho", "Vehnäjauho", "VEHNÄJAUHO", "vähittäis"):
            with self.subTest(query=query):
                results = self.client.get(reverse("search_fineli_api"), {"q": query}).json()["results"]
                self.assertEqual([r["id"] for r in results], [34249])

    def test_custom_food_search_matches_umlauts(self):
        CustomFood.objects.create(user=self.user, name="Vaahterasiirappi", brand="Änglamark", energy_kcal_per_100g=270)
        for query in ("änglamark", "ÄNGLAMARK", "Änglamark"):
            with self.subTest(query=query):
                results = self.client.get(reverse("list_custom_foods_api"), {"q": query}).json()["results"]
                self.assertEqual([r["name"] for r in results], ["Vaahterasiirappi"])

    def test_ascii_search_still_case_insensitive(self):
        results = self.client.get(reverse("search_fineli_api"), {"q": "jauho"}).json()["results"]
        self.assertEqual([r["id"] for r in results], [34249])

    def test_recipe_page_has_live_search(self):
        resp = self.client.get(reverse("recipe_detail", args=[self.recipe.id]))
        self.assertContains(resp, 'id="fineliSearchInput"')
        self.assertContains(resp, reverse("search_fineli_api"))
        self.assertContains(resp, reverse("add_recipe_fineli_ingredient", args=[self.recipe.id]))

    def test_every_word_must_match_in_any_order(self):
        FineliFood.objects.create(fineli_id=10, name_fi="MAITO, KEVYTMAITO, LAKTOOSITON MAITOJUOMA", energy_kcal=40)
        FineliFood.objects.create(fineli_id=11, name_fi="MAITO, KEVYTMAITO", energy_kcal=40)
        FineliFood.objects.create(fineli_id=12, name_fi="JOGURTTI, LAKTOOSITON", energy_kcal=60)
        for query in ("maito lak", "lak maito", "Maito, laktoositon", "kevyt LAKTOOS"):
            with self.subTest(query=query):
                results = self.client.get(reverse("search_fineli_api"), {"q": query}).json()["results"]
                self.assertEqual([r["id"] for r in results], [10])
        resp = self.client.get(reverse("recipe_detail", args=[self.recipe.id]), {"q": "maito lak"})
        self.assertEqual([f.fineli_id for f in resp.context["fineli_results"]], [10])

    def test_custom_food_search_multiple_words(self):
        CustomFood.objects.create(user=self.user, name="Vaahterasiirappi", brand="Änglamark", energy_kcal_per_100g=270)
        CustomFood.objects.create(user=self.user, name="Vaahterasiirappi", brand="Rainbow", energy_kcal_per_100g=270)
        results = self.client.get(reverse("list_custom_foods_api"), {"q": "siirappi änglamark"}).json()["results"]
        self.assertEqual([r["brand"] for r in results], ["Änglamark"])

    def test_best_match_ranked_first(self):
        FineliFood.objects.create(fineli_id=1, name_fi="(ARC)PAAHTOLEIPÄ, FAZER, VEHNÄJAUHO", energy_kcal=250)
        FineliFood.objects.create(fineli_id=2, name_fi="JAUHO, VEHNÄJAUHO, PUOLIKARKEA", energy_kcal=340)
        FineliFood.objects.create(fineli_id=3, name_fi="BANAANILASTU, KUIVATTU BANAANI", energy_kcal=500)
        FineliFood.objects.create(fineli_id=4, name_fi="BANAANI, KUORITTU", energy_kcal=90)
        resp = self.client.get(reverse("recipe_detail", args=[self.recipe.id]), {"q": "vehnäjauho"})
        self.assertEqual(resp.context["fineli_results"][0].fineli_id, 2)
        results = self.client.get(reverse("search_fineli_api"), {"q": "banaani"}).json()["results"]
        self.assertEqual([r["id"] for r in results], [4, 3])

    def test_diary_search_keeps_most_used_first(self):
        FineliFood.objects.create(fineli_id=4, name_fi="BANAANI, KUORITTU", energy_kcal=90)
        FineliFood.objects.create(fineli_id=3, name_fi="BANAANILASTU, KUIVATTU BANAANI", energy_kcal=500, usage_count=5)
        results = self.client.get(reverse("search_fineli_api"), {"q": "banaani"}).json()["results"]
        self.assertEqual([r["id"] for r in results], [3, 4])


class SmarterFoodSearchTests(TestCase):
    """Diary food search: Finnish compounds ("marjahillo" is jam), English
    names, synonyms, typos, all matches reachable, and own foods included."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="smartsearch", password="x")
        self.client.force_login(self.user)

    def _ids(self, query, **params):
        return [r["id"] for r in self._search(query, **params)["results"]]

    def _search(self, query, **params):
        return self.client.get(reverse("search_fineli_api"), {"q": query, **params}).json()

    def test_compound_ending_in_word_ranks_above_one_starting_with_it(self):
        for i in range(25):
            FineliFood.objects.create(fineli_id=100 + i, name_fi=f"HILLOPIIRAKKA, PULLAPOHJA {i}", energy_kcal=300)
        FineliFood.objects.create(fineli_id=1, name_fi="MARJAHILLO, KESKIARVO", energy_kcal=200)
        FineliFood.objects.create(fineli_id=2, name_fi="HILLO, FRUKTOOSILLA MAKEUTETTU", energy_kcal=150)
        FineliFood.objects.create(fineli_id=3, name_fi="JOGURTTI, MAUSTETTU HILLOLLA", energy_kcal=80)
        ids = self._ids("hillo")
        self.assertEqual(ids[:2], [2, 1])
        self.assertNotIn(3, ids)  # only flavoured with jam: behind the 25 pies

    def test_compound_head_also_after_a_hyphen(self):
        FineliFood.objects.create(fineli_id=1, name_fi="RIISI PUUROUTUVA", energy_kcal=350)
        FineliFood.objects.create(fineli_id=2, name_fi="KAURA-RUISPUURO, VESI, SUOLAA", energy_kcal=60)
        FineliFood.objects.create(fineli_id=3, name_fi="KAURAPUURO, VESI, SUOLAA", energy_kcal=60)
        self.assertEqual(self._ids("puuro"), [3, 2, 1])

    def test_english_name_counts_for_ranking(self):
        FineliFood.objects.create(fineli_id=1, name_fi="HILLOPIIRAKKA, MUROPOHJA", name_en="JAM PIE, SHORTBREAD CRUST", energy_kcal=300)
        FineliFood.objects.create(fineli_id=2, name_fi="MARJAHILLO, KESKIARVO", name_en="MIXED BERRY JAM", energy_kcal=200)
        self.assertEqual(self._ids("jam"), [2, 1])

    def test_synonym_matches_after_the_typed_word(self):
        FineliFood.objects.create(fineli_id=1, name_fi="BROILERI, RINTALEIKE, RAAKA", energy_kcal=110)
        FineliFood.objects.create(fineli_id=2, name_fi="KANA, KEITETTY", energy_kcal=150)
        FineliFood.objects.create(fineli_id=3, name_fi="KANANMUNA, KEITETTY", energy_kcal=140)
        self.assertEqual(self._ids("kana"), [2, 1, 3])

    def test_custom_foods_match_synonyms(self):
        CustomFood.objects.create(user=self.user, name="Broilerin fileesuikale", energy_kcal_per_100g=110)
        results = self.client.get(reverse("list_custom_foods_api"), {"q": "kana"}).json()["results"]
        self.assertEqual([r["name"] for r in results], ["Broilerin fileesuikale"])

    def test_total_and_limit(self):
        for i in range(25):
            FineliFood.objects.create(fineli_id=100 + i, name_fi=f"MAITO, {i}", energy_kcal=40)
        data = self._search("maito")
        self.assertEqual((data["count"], data["total"]), (20, 25))
        self.assertEqual(self._search("maito", limit=25)["count"], 25)
        self.assertEqual(self._search("maito", limit=5000)["count"], 25)  # capped at 200
        self.assertEqual(self._search("maito", limit="x")["count"], 20)
        self.assertEqual(self._search("maito", limit=0)["count"], 1)

    def test_typo_is_corrected(self):
        FineliFood.objects.create(fineli_id=1, name_fi="JOGURTTI, MAUSTAMATON", energy_kcal=60)
        for query in ("jugurtti", "jugurt", "Jogurtii"):
            with self.subTest(query=query):
                data = self._search(query)
                self.assertEqual([r["id"] for r in data["results"]], [1])
                self.assertTrue(data["corrected_query"])

    def test_typo_corrected_only_in_the_unmatched_word(self):
        FineliFood.objects.create(fineli_id=1, name_fi="JOGURTTI, MAUSTAMATON", energy_kcal=60)
        data = self._search("jugurtti maustamaton")
        self.assertEqual(data["corrected_query"], "jogurtti maustamaton")

    def test_no_correction_when_found_or_nothing_close(self):
        FineliFood.objects.create(fineli_id=1, name_fi="JOGURTTI, MAUSTAMATON", energy_kcal=60)
        self.assertIsNone(self._search("jogurtti")["corrected_query"])
        data = self._search("xqzw")
        self.assertEqual((data["results"], data["corrected_query"]), ([], None))

    def test_typo_vocabulary_follows_new_foods(self):
        FineliFood.objects.create(fineli_id=1, name_fi="JOGURTTI", energy_kcal=60)
        self._search("jugurtti")
        FineliFood.objects.create(fineli_id=2, name_fi="PORKKANA, RAAKA", energy_kcal=30)
        self.assertEqual([r["id"] for r in self._search("porkana")["results"]], [2])


class FoodDiaryAddFoodLayoutTests(TestCase):
    """Add Food: the ways of adding are folded away (search is the default),
    and the diary CSV import lives in the menu only."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="addlayout", password="x")
        self.client.force_login(self.user)
        self.resp = self.client.get(reverse("food_diary"))

    def test_tabs_folded_by_default(self):
        self.assertContains(self.resp, 'id="tabsToggle"')
        self.assertContains(self.resp, 'aria-expanded="false"')
        self.assertContains(self.resp, '<div class="modal-tabs" id="modalTabs" hidden>', html=False)

    def test_no_csv_import_in_add_food_or_header(self):
        self.assertNotContains(self.resp, 'data-tab="csv"')
        self.assertNotContains(self.resp, 'bulk-import-btn')
        # The import dialog itself is still on the page, opened from the menu.
        self.assertContains(self.resp, 'id="bulkImportModal"')

    def test_menu_links_to_csv_import(self):
        self.assertContains(self.resp, reverse("food_diary") + "#import-csv")

    def test_search_includes_own_foods(self):
        self.assertContains(self.resp, "/food-diary/api/custom-foods/?q=")


class RecipePickerTests(TestCase):
    """Recipe page pickers: choose a food from a list (Fineli suggestions,
    Fineli favorites, My Foods), then set the amount in a panel whose fields
    post to the existing add-ingredient endpoints."""

    def setUp(self):
        from .models import FineliFoodUnit
        self.user = get_user_model().objects.create_user(username="recipepicker", password="x")
        self.client.force_login(self.user)
        self.recipe = CustomFood.objects.create(user=self.user, name="Pannukakku")
        self.flour = FineliFood.objects.create(fineli_id=2, name_fi="JAUHO, VEHNÄJAUHO", energy_kcal=340)
        FineliFoodUnit.objects.create(fineli_food=self.flour, unit_code="DL", mass_g=60)
        FavoriteFood.objects.create(user=self.user, fineli_food=self.flour)
        self.syrup = CustomFood.objects.create(user=self.user, name="Vaahterasiirappi", brand="Änglamark", use_count=3)
        self.url = reverse("recipe_detail", args=[self.recipe.id])

    def test_favorites_listed_with_their_portion_units(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["favorite_fineli_foods"], [self.flour])
        self.assertContains(resp, 'data-pick="fineli" data-id="2"')
        self.assertContains(resp, 'data-units="DL|dl|60.0"')

    def test_other_users_favorites_not_listed(self):
        other = get_user_model().objects.create_user(username="otherpicker", password="x")
        oats = FineliFood.objects.create(fineli_id=3, name_fi="KAURAHIUTALE", energy_kcal=380)
        FavoriteFood.objects.create(user=other, fineli_food=oats)
        self.assertEqual(self.client.get(self.url).context["favorite_fineli_foods"], [self.flour])

    def test_favorites_hidden_while_showing_search_results(self):
        resp = self.client.get(self.url, {"q": "jauho"})
        self.assertContains(resp, 'id="fineliFavorites" hidden')

    def test_own_foods_are_a_searchable_pick_list(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, 'id="ownFoodSearch"')
        self.assertContains(resp, 'data-search="vaahterasiirappi änglamark"')
        self.assertNotContains(resp, f'data-pick="own" data-id="{self.recipe.id}"')  # never itself

    def test_own_foods_most_used_first(self):
        CustomFood.objects.create(user=self.user, name="Aaa-tuote", use_count=0)
        names = [f.name for f in self.client.get(self.url).context["own_foods"]]
        self.assertEqual(names, ["Vaahterasiirappi", "Aaa-tuote"])

    def test_amount_panels_post_expected_fields(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, reverse("add_recipe_fineli_ingredient", args=[self.recipe.id]))
        self.assertContains(resp, 'name="fineli_id"')
        self.assertContains(resp, 'name="amount"')
        self.assertContains(resp, 'name="unit"')
        self.assertContains(resp, reverse("add_recipe_custom_ingredient", args=[self.recipe.id]))
        self.assertContains(resp, 'name="source_custom_food_id"')
        self.assertContains(resp, 'name="amount_grams"')

    def test_adding_favorite_by_portion_unit(self):
        self.client.post(reverse("add_recipe_fineli_ingredient", args=[self.recipe.id]),
                         {"fineli_id": 2, "amount": "2", "unit": "DL"})
        self.assertEqual(self.recipe.ingredients.get().amount_grams, 120)


class FavoritesPageTests(TestCase):
    """Nutrition -> Favorites: the user's own starred foods, searchable to add more."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="favpage", password="x")
        self.client.force_login(self.user)
        self.flour = FineliFood.objects.create(fineli_id=2, name_fi="JAUHO, VEHNÄJAUHO", energy_kcal=340)
        self.syrup = CustomFood.objects.create(user=self.user, name="Vaahterasiirappi", brand="Änglamark")
        FavoriteFood.objects.create(user=self.user, fineli_food=self.flour)
        FavoriteFood.objects.create(user=self.user, custom_food=self.syrup)
        self.url = reverse("manage_favorites")

    def test_lists_own_fineli_and_custom_favorites(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, 'data-kind="fineli" data-id="2"')
        self.assertContains(resp, f'data-kind="custom" data-id="{self.syrup.id}"')
        self.assertContains(resp, "Änglamark")

    def test_other_users_favorites_not_shown(self):
        other = get_user_model().objects.create_user(username="favother", password="x")
        oats = FineliFood.objects.create(fineli_id=3, name_fi="KAURAHIUTALE", energy_kcal=380)
        FavoriteFood.objects.create(user=other, fineli_food=oats)
        self.assertNotContains(self.client.get(self.url), "KAURAHIUTALE")

    def test_page_uses_favorites_api(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, reverse("add_favorite_food"))
        self.assertContains(resp, reverse("remove_favorite_food"))

    def test_menu_has_favorites_entry(self):
        resp = self.client.get(reverse("food_diary"))
        self.assertContains(resp, f'href="{self.url}"')

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)


class RecipeAutoRecalculateTests(TestCase):
    """Adding or removing an ingredient recalculates the recipe at once and
    tells the user; saves show a confirmation."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="autorecalc", password="x")
        self.client.force_login(self.user)
        self.recipe = CustomFood.objects.create(user=self.user, name="Pannukakku")
        self.oats = FineliFood.objects.create(fineli_id=153, name_fi="KAURAHIUTALE", energy_kcal=380, protein_g=13)

    def _add_oats(self, grams="100"):
        return self.client.post(reverse("add_recipe_fineli_ingredient", args=[self.recipe.id]),
                                {"fineli_id": 153, "amount": grams, "unit": "G"}, follow=True)

    def test_adding_ingredient_recalculates(self):
        resp = self._add_oats()
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.energy_kcal_per_100g, 380)
        self.assertIn("Added KAURAHIUTALE (100 g) - nutrition values recalculated.",
                      [str(m) for m in resp.context["messages"]])

    def test_removing_ingredient_recalculates(self):
        self._add_oats()
        ingredient = self.recipe.ingredients.get()
        resp = self.client.post(reverse("remove_recipe_ingredient", args=[self.recipe.id, ingredient.id]), follow=True)
        self.recipe.refresh_from_db()
        self.assertIsNone(self.recipe.energy_kcal_per_100g)
        self.assertIn("Removed KAURAHIUTALE - nutrition values recalculated.", [str(m) for m in resp.context["messages"]])

    def test_adding_custom_ingredient_recalculates(self):
        quark = CustomFood.objects.create(user=self.user, name="Rahka", energy_kcal_per_100g=60, protein_per_100g=10)
        self.client.post(reverse("add_recipe_custom_ingredient", args=[self.recipe.id]),
                         {"source_custom_food_id": quark.id, "amount_grams": "200"})
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.energy_kcal_per_100g, 60)

    def test_saving_final_values_confirms(self):
        resp = self.client.post(reverse("save_recipe_final_values", args=[self.recipe.id]),
                                {"energy_kcal_per_100g": "250"}, follow=True)
        self.assertIn("Final nutrition values saved.", [str(m) for m in resp.context["messages"]])

    def test_messages_rendered_as_escaped_toasts(self):
        evil = FineliFood.objects.create(fineli_id=999, name_fi="<b>X</b>", energy_kcal=1)
        resp = self.client.post(reverse("add_recipe_fineli_ingredient", args=[self.recipe.id]),
                                {"fineli_id": 999, "amount": "1", "unit": "G"}, follow=True)
        self.assertContains(resp, "showToast(")
        self.assertNotContains(resp, 'showToast("Added <b>')


class RecipeListPageTests(TestCase):
    """Nutrition -> Recipes lists only recipes (with ingredients, or a fresh
    empty recipe shell) as cards, with Create Recipe."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="recipelist", password="x")
        self.client.force_login(self.user)
        oats = FineliFood.objects.create(fineli_id=153, name_fi="KAURAHIUTALE", energy_kcal=380)
        self.recipe = CustomFood.objects.create(user=self.user, name="Puuro", energy_kcal_per_100g=120)
        CustomFoodIngredient.objects.create(custom_food=self.recipe, fineli_food=oats, amount_grams=60)
        self.shell = CustomFood.objects.create(user=self.user, name="Uusi resepti", kind=CustomFood.KIND_RECIPE)
        self.product = CustomFood.objects.create(user=self.user, name="Rahka", energy_kcal_per_100g=60)
        self.url = reverse("recipe_list")

    def test_lists_recipes_not_products(self):
        names = [r.name for r in self.client.get(self.url).context["recipes"]]
        self.assertEqual(names, ["Puuro", "Uusi resepti"])

    def test_card_shows_summary_and_details(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, "1 ingredient &middot; 60 g total")
        self.assertContains(resp, "<summary>Show details</summary>", html=False)
        self.assertContains(resp, "KAURAHIUTALE")
        self.assertContains(resp, reverse("recipe_detail", args=[self.recipe.id]))

    def test_create_recipe_form_on_page(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, f'action="{reverse("create_recipe")}"')
        resp = self.client.post(reverse("create_recipe"), {"name": "Pannukakku", "brand": ""})
        created = CustomFood.objects.get(name="Pannukakku")
        self.assertRedirects(resp, reverse("recipe_detail", args=[created.id]))
        self.assertIn("Pannukakku", [r.name for r in self.client.get(self.url).context["recipes"]])

    def test_other_users_recipes_hidden(self):
        other = get_user_model().objects.create_user(username="recipeother", password="x")
        CustomFood.objects.create(user=other, name="Vieras resepti")
        self.assertNotContains(self.client.get(self.url), "Vieras resepti")

    def test_menu_recipes_link_points_to_list(self):
        self.assertContains(self.client.get(reverse("food_diary")), f'href="{self.url}"')

    def test_delete_url_get_redirects_instead_of_crashing(self):
        resp = self.client.get(reverse("delete_custom_food", args=[self.product.id]))
        self.assertRedirects(resp, reverse("manage_custom_foods"))
        self.assertTrue(CustomFood.objects.filter(pk=self.product.pk).exists())


class OwnFoodBrowserTests(TestCase):
    """Recipes and My products: search (recipes also by ingredient), sort,
    and a compact A-Ö list next to the cards (nutrition/item_browser.js)."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="browser", password="x")
        self.client.force_login(self.user)
        oats = FineliFood.objects.create(fineli_id=153, name_fi="KAURAHIUTALE", energy_kcal=380)
        self.recipe = CustomFood.objects.create(user=self.user, name="Puuro", energy_kcal_per_100g=120.5, protein_per_100g=4.25)
        CustomFoodIngredient.objects.create(custom_food=self.recipe, fineli_food=oats, amount_grams=60)
        self.product = CustomFood.objects.create(user=self.user, name="Rahka", brand="Valio", energy_kcal_per_100g=60, protein_per_100g=10.5)

    def test_recipes_page_has_browser_and_list(self):
        resp = self.client.get(reverse("recipe_list"))
        self.assertContains(resp, 'data-browser="recipes"')
        self.assertContains(resp, 'data-browser-sort')
        self.assertContains(resp, 'data-browser-rows')
        self.assertContains(resp, "nutrition/item_browser.js")
        # One card and one list row per recipe, both opening the recipe.
        self.assertContains(resp, f'data-item="{self.recipe.id}"', count=2)
        self.assertContains(resp, f'href="{reverse("recipe_detail", args=[self.recipe.id])}"', count=2)

    def test_recipe_search_covers_ingredients(self):
        resp = self.client.get(reverse("recipe_list"))
        self.assertContains(resp, 'data-search="puuro  kaurahiutale"')

    def test_sort_options(self):
        resp = self.client.get(reverse("recipe_list"))
        for value in ("name", "uses", "updated"):
            self.assertContains(resp, f'<option value="{value}">', html=False)
        self.assertContains(resp, 'data-uses="0"')
        self.assertNotContains(resp, 'value="kcal"')

    def test_products_page_has_browser_and_list(self):
        resp = self.client.get(reverse("manage_custom_foods"))
        self.assertContains(resp, 'data-browser="products"')
        self.assertContains(resp, f'data-item="{self.product.id}"', count=2)
        self.assertContains(resp, 'data-search="rahka valio"')
        self.assertContains(resp, f'href="{reverse("edit_custom_food", args=[self.product.id])}"', count=2)
        self.assertNotContains(resp, f'data-item="{self.recipe.id}"')

    def test_products_info_behind_info_button(self):
        resp = self.client.get(reverse("manage_custom_foods"))
        self.assertContains(resp, 'id="productsInfoToggle" aria-expanded="false"')
        self.assertContains(resp, '<p class="info-text" id="productsInfo" hidden>', html=False)
        # The jump-to-form and Recipes buttons are gone (both are in the menu / on the page).
        self.assertNotContains(resp, 'href="#add-product-form"')
        self.assertNotContains(resp, 'class="btn btn-outline-light">🧪')

    def test_add_product_form_folded_until_needed(self):
        resp = self.client.get(reverse("manage_custom_foods"))
        self.assertContains(resp, '<details class="add-form" id="add-product-form">', html=False)
        # A failed save opens it again, with the errors.
        resp = self.client.post(reverse("manage_custom_foods"), {"name": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '<details class="add-form" id="add-product-form" open>', html=False)

    def test_no_browser_without_items(self):
        CustomFood.objects.filter(user=self.user).delete()
        self.assertNotContains(self.client.get(reverse("recipe_list")), "data-browser=")
        self.assertNotContains(self.client.get(reverse("manage_custom_foods")), "data-browser=")


class RecipeKindAndYieldTests(TestCase):
    """Products and recipes are separate (CustomFood.kind); recipes have an
    optional servings count and finished-dish weight, and can be renamed,
    duplicated and deleted."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="recipekind", password="x")
        self.client.force_login(self.user)
        self.oats = FineliFood.objects.create(fineli_id=153, name_fi="KAURAHIUTALE", energy_kcal=380, protein_g=13)
        self.client.post(reverse("create_recipe"), {"name": "Puuro", "brand": ""})
        self.recipe = CustomFood.objects.get(name="Puuro")
        self.client.post(reverse("add_recipe_fineli_ingredient", args=[self.recipe.id]),
                         {"fineli_id": 153, "amount": "200", "unit": "G"})
        self.product = CustomFood.objects.create(user=self.user, name="Rahka", energy_kcal_per_100g=60)
        self.recipe.refresh_from_db()

    def _settings(self, **fields):
        data = {"name": self.recipe.name, "brand": "", "servings": "", "cooked_weight_g": ""}
        data.update(fields)
        return self.client.post(reverse("update_recipe_settings", args=[self.recipe.id]), data, follow=True)

    def _log(self, food, unit, amount):
        import json as _json
        return self.client.post(reverse("add_fineli_entry"), data=_json.dumps({
            "date": "2026-10-12", "source": "custom", "custom_food_id": food.id, "name": food.name,
            "meal_type": "breakfast", "amount": amount, "unit": unit, "portion_grams": 1,
            "energy_kcal": food.energy_kcal_per_100g,
        }), content_type="application/json")

    # --- product / recipe split ---
    def test_create_recipe_marks_kind_recipe(self):
        self.assertEqual(self.recipe.kind, CustomFood.KIND_RECIPE)
        self.assertEqual(self.product.kind, CustomFood.KIND_PRODUCT)

    def test_adding_an_ingredient_makes_a_food_a_recipe(self):
        CustomFoodIngredient.objects.create(custom_food=self.product, fineli_food=self.oats, amount_grams=10)
        self.product.refresh_from_db()
        self.assertEqual(self.product.kind, CustomFood.KIND_RECIPE)

    def test_foods_page_lists_products_only(self):
        names = {f.name for f in self.client.get(reverse("manage_custom_foods")).context["custom_foods"]}
        self.assertEqual(names, {"Rahka"})

    def test_recipes_page_lists_recipes_only(self):
        names = [r.name for r in self.client.get(reverse("recipe_list")).context["recipes"]]
        self.assertEqual(names, ["Puuro"])

    def test_diary_api_flags_recipes_by_kind(self):
        flags = {f["name"]: f["is_recipe"] for f in self.client.get(reverse("list_custom_foods_api")).json()["results"]}
        self.assertEqual(flags, {"Puuro": True, "Rahka": False})

    # --- servings: optional, both ways of logging ---
    def test_without_servings_only_grams(self):
        api = {f["name"]: f for f in self.client.get(reverse("list_custom_foods_api")).json()["results"]}
        self.assertIsNone(api["Puuro"]["serving_grams"])
        self.assertEqual(self._log(self.recipe, "SERV", 1).status_code, 400)
        self.assertEqual(self._log(self.recipe, "G", 150).status_code, 200)

    def test_servings_allow_logging_one_serving_or_grams(self):
        self._settings(servings="4")  # 200 g recipe -> 50 g per serving
        api = {f["name"]: f for f in self.client.get(reverse("list_custom_foods_api")).json()["results"]}
        self.assertEqual(api["Puuro"]["serving_grams"], 50)
        self.assertEqual(self._log(self.recipe, "SERV", 2).status_code, 200)
        entry = MealEntry.objects.get(source_unit="SERV")
        self.assertEqual(entry.portion_grams, 100)
        self.assertEqual(entry.portion_label, "2 × serving")
        self.assertEqual(self._log(self.recipe, "G", 30).status_code, 200)

    def test_products_cannot_be_logged_as_servings(self):
        self.assertEqual(self._log(self.product, "SERV", 1).status_code, 400)

    # --- finished dish weight ---
    def test_cooked_weight_changes_per_100g_basis(self):
        self.assertAlmostEqual(self.recipe.energy_kcal_per_100g, 380)
        resp = self._settings(cooked_weight_g="400")  # porridge doubles with water
        self.recipe.refresh_from_db()
        self.assertAlmostEqual(self.recipe.energy_kcal_per_100g, 190)
        self.assertIn("Recipe details saved - nutrition values recalculated.",
                      [str(m) for m in resp.context["messages"]])

    def test_serving_uses_cooked_weight(self):
        self._settings(cooked_weight_g="400", servings="4")
        api = {f["name"]: f for f in self.client.get(reverse("list_custom_foods_api")).json()["results"]}
        self.assertEqual(api["Puuro"]["serving_grams"], 100)

    def test_rename_and_clear_yield(self):
        self._settings(name="Kaurapuuro", servings="2", cooked_weight_g="400")
        self.recipe.refresh_from_db()
        self._settings(name="Kaurapuuro")
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.name, "Kaurapuuro")
        self.assertIsNone(self.recipe.servings)
        self.assertIsNone(self.recipe.cooked_weight_g)
        self.assertAlmostEqual(self.recipe.energy_kcal_per_100g, 380)

    def test_invalid_yield_rejected(self):
        resp = self._settings(servings="0", cooked_weight_g="-5")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("servings", resp.context["settings_form"].errors)
        self.assertIn("cooked_weight_g", resp.context["settings_form"].errors)

    # --- duplicate / delete ---
    def test_duplicate_copies_ingredients_and_values(self):
        self._settings(servings="4")
        resp = self.client.post(reverse("duplicate_recipe", args=[self.recipe.id]))
        copy = CustomFood.objects.get(name="Puuro (copy)")
        self.assertRedirects(resp, reverse("recipe_detail", args=[copy.id]))
        self.assertEqual(copy.kind, CustomFood.KIND_RECIPE)
        self.assertEqual(copy.servings, 4)
        self.assertEqual(copy.ingredients.get().amount_grams, 200)
        self.assertAlmostEqual(copy.energy_kcal_per_100g, 380)
        self.assertEqual(self.recipe.ingredients.count(), 1)

    def test_delete_recipe_keeps_logged_diary_entries(self):
        self._log(self.recipe, "G", 100)
        resp = self.client.post(reverse("delete_recipe", args=[self.recipe.id]))
        self.assertRedirects(resp, reverse("recipe_list"))
        self.assertFalse(CustomFood.objects.filter(pk=self.recipe.pk).exists())
        entry = MealEntry.objects.get()
        self.assertIsNone(entry.custom_food)
        self.assertAlmostEqual(entry.calories, 380)

    def test_food_used_in_another_recipe_cannot_be_deleted(self):
        other = CustomFood.objects.create(user=self.user, name="Iso puuro", kind=CustomFood.KIND_RECIPE)
        CustomFoodIngredient.objects.create(custom_food=other, source_custom_food=self.recipe, amount_grams=50)
        CustomFoodIngredient.objects.create(custom_food=other, source_custom_food=self.product, amount_grams=50)
        resp = self.client.post(reverse("delete_recipe", args=[self.recipe.id]), follow=True)
        self.assertTrue(CustomFood.objects.filter(pk=self.recipe.pk).exists())
        self.assertIn("Puuro is an ingredient in Iso puuro - remove it there first.",
                      [str(m) for m in resp.context["messages"]])
        self.client.post(reverse("delete_custom_food", args=[self.product.id]))
        self.assertTrue(CustomFood.objects.filter(pk=self.product.pk).exists())
        self.assertEqual(other.ingredients.count(), 2)

    def test_cannot_delete_or_duplicate_other_users_recipe(self):
        other = get_user_model().objects.create_user(username="recipekindother", password="x")
        self.client.force_login(other)
        self.assertEqual(self.client.post(reverse("delete_recipe", args=[self.recipe.id])).status_code, 404)
        self.assertEqual(self.client.post(reverse("duplicate_recipe", args=[self.recipe.id])).status_code, 404)

    def test_recipe_page_has_details_form_and_actions(self):
        resp = self.client.get(reverse("recipe_detail", args=[self.recipe.id]))
        self.assertContains(resp, reverse("update_recipe_settings", args=[self.recipe.id]))
        self.assertContains(resp, 'name="servings"')
        self.assertContains(resp, 'name="cooked_weight_g"')
        self.assertContains(resp, 'data-confirm="Click again to delete"')


class LocalDateTests(TestCase):
    """'Today' follows Finnish time, not UTC: 22:30 UTC on 12.10. is already
    01:30 on 13.10. in Helsinki, and food logged then belongs to the 13th."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="localdate", password="x")
        self.client.force_login(self.user)

    def _at_utc(self, *args):
        import datetime as _dt
        from unittest import mock
        moment = _dt.datetime(*args, tzinfo=_dt.timezone.utc)
        return mock.patch("django.utils.timezone.now", return_value=moment)

    def test_diary_opens_local_today_after_midnight(self):
        import datetime as _dt
        with self._at_utc(2026, 10, 12, 22, 30):
            resp = self.client.get(reverse("food_diary"))
        self.assertEqual(resp.context["current_date"], _dt.date(2026, 10, 13))
        self.assertTrue(resp.context["is_today"])

    def test_entry_without_date_goes_to_local_today(self):
        import json as _json
        with self._at_utc(2026, 10, 12, 22, 30):
            self.client.post(reverse("add_fineli_entry"), data=_json.dumps({
                "name": "Iltapala", "meal_type": "snack", "portion_grams": 100, "energy_kcal": 100,
            }), content_type="application/json")
        self.assertEqual(str(MealEntry.objects.get().daily_log.date), "2026-10-13")


class DiaryResultMarkupTests(TestCase):
    """Result rows must not inline the food as JSON in a quoted onclick
    attribute - a name with an apostrophe (MCDONALD'S) broke it."""

    def test_results_reference_foods_by_index(self):
        user = get_user_model().objects.create_user(username="diarymarkup", password="x")
        self.client.force_login(user)
        resp = self.client.get(reverse("food_diary"))
        self.assertNotContains(resp, "JSON.stringify(food)})'")
        self.assertContains(resp, "shownFoods[${foodRef(food)}]")
        self.assertContains(resp, "function escapeHtml(")
        self.assertContains(resp, "serving_grams: food.serving_grams")

    def test_apostrophe_names_are_searchable(self):
        FineliFood.objects.create(fineli_id=34395, name_fi="HAMPURILAINEN, MCDONALD'S", energy_kcal=250)
        results = self.client.get(reverse("search_fineli_api"), {"q": "mcdonald's"}).json()["results"]
        self.assertEqual([r["name"] for r in results], ["HAMPURILAINEN, MCDONALD'S"])


class EditMealEntryTests(TestCase):
    """Diary entries can be edited: grams or the portion count change the
    nutrient values proportionally (including micronutrients and the day's
    totals) unless the values are corrected by hand."""

    def setUp(self):
        from .models import FineliFoodUnit
        self.user = get_user_model().objects.create_user(username="editentry", password="x")
        self.client.force_login(self.user)
        self.banana = FineliFood.objects.create(fineli_id=11049, name_fi="BANAANI, KUORITTU", energy_kcal=90, protein_g=1.2)
        FineliFoodNutrient.objects.create(fineli_food=self.banana, nutrient_key="potassium", value=360, unit="mg")
        FineliFoodUnit.objects.create(fineli_food=self.banana, unit_code="KPL_L", mass_g=150)
        import json as _json
        self.client.post(reverse("add_fineli_entry"), data=_json.dumps({
            "date": "2026-10-12", "fineli_id": 11049, "name": "BANAANI, KUORITTU", "meal_type": "snack",
            "amount": 2, "unit": "KPL_L", "energy_kcal": 90, "protein": 1.2,
        }), content_type="application/json")
        self.entry = MealEntry.objects.get()
        self.url = reverse("edit_meal_entry", args=[self.entry.id])

    def _post(self, **fields):
        data = {
            "food_name": self.entry.food_name, "meal_type": self.entry.meal_type,
            "portion_grams": repr(self.entry.portion_grams), "portion_amount": "",
            "notes": self.entry.notes or "",
        }
        for name in ("calories", "protein_g", "carbs_g", "sugar_g", "fat_g", "sat_fat_g", "fiber_g", "salt_g"):
            value = getattr(self.entry, name)
            data[name] = "" if value is None else repr(value)
        data.update(fields)
        resp = self.client.post(self.url, data)
        self.entry.refresh_from_db()
        return resp

    def test_edit_page_has_no_meal_picker(self):
        resp = self.client.get(self.url)
        self.assertNotContains(resp, "meal-combo")
        self.assertContains(resp, '<input type="hidden" name="meal_type" value="Snack">', html=True)

    def test_saving_with_the_shown_meal_keeps_the_stored_one(self):
        MealEntry.objects.filter(pk=self.entry.pk).update(meal_type="Herkku")
        hidden = self.client.get(self.url).context["form"]["meal_type"].value()
        self._post(meal_type=hidden, food_name="Banaani")
        self.assertEqual((self.entry.meal_type, self.entry.food_name), ("Herkku", "Banaani"))

    def test_edit_page_renders(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Amount (× large piece)")
        self.assertContains(resp, 'step="any"')

    def test_diary_has_edit_link(self):
        self.assertContains(self.client.get(reverse("food_diary"), {"date": "2026-10-12"}), self.url)

    def test_changing_grams_scales_values_and_clears_portion(self):
        self.assertEqual((self.entry.portion_grams, self.entry.calories), (300, 270))
        resp = self._post(portion_grams="150", portion_amount="2")
        self.assertRedirects(resp, reverse("food_diary") + "?date=2026-10-12")
        self.assertEqual(self.entry.portion_grams, 150)
        self.assertAlmostEqual(self.entry.calories, 135)
        self.assertAlmostEqual(self.entry.protein_g, 1.8)
        self.assertEqual(self.entry.portion_label, "")
        self.assertAlmostEqual(self.entry.nutrients.get(nutrient_key="potassium").value, 540)
        self.assertEqual(self.entry.daily_log.calories_in, 135)

    def test_changing_portion_count_keeps_unit(self):
        self._post(portion_amount="3")
        self.assertEqual(self.entry.portion_grams, 450)
        self.assertAlmostEqual(self.entry.calories, 405)
        self.assertEqual(self.entry.portion_label, "3 × large piece")

    def test_hand_corrected_values_are_kept(self):
        self._post(portion_grams="150", calories="200")
        self.assertAlmostEqual(self.entry.calories, 200)
        self.assertAlmostEqual(self.entry.protein_g, 3.6)  # submitted unchanged, not scaled

    def test_unknown_value_stays_unknown(self):
        self._post(portion_grams="150")
        self.assertIsNone(self.entry.fat_g)

    def test_meal_and_name_only(self):
        self._post(meal_type="dinner", food_name="Banaani")
        self.assertEqual((self.entry.meal_type, self.entry.food_name, self.entry.calories), ("dinner", "Banaani", 270))
        self.assertEqual(self.entry.portion_label, "2 × large piece")

    def test_imported_meal_name_is_accepted(self):
        MealEntry.objects.filter(pk=self.entry.pk).update(meal_type="Herkku")
        self.entry.refresh_from_db()
        resp = self._post()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.entry.meal_type, "Herkku")

    def test_other_user_cannot_edit(self):
        other = get_user_model().objects.create_user(username="editentryother", password="x")
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_delete_url_get_redirects(self):
        resp = self.client.get(reverse("delete_meal_entry", args=[self.entry.id]))
        self.assertRedirects(resp, reverse("food_diary") + "?date=2026-10-12")
        self.assertTrue(MealEntry.objects.filter(pk=self.entry.pk).exists())


    def test_own_meal_name(self):
        resp = self._post(meal_type="  Ennen   kisaa ")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.entry.meal_type, "Ennen kisaa")

    def test_standard_meal_name_uses_its_key(self):
        self._post(meal_type="lounas")
        self.assertEqual(self.entry.meal_type, "lunch")
        self._post(meal_type="Illallinen")
        self.assertEqual(self.entry.meal_type, "evening_snack")

    def test_meal_is_required(self):
        resp = self._post(meal_type=" ")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.entry.meal_type, "snack")

    def test_unchanged_meal_keeps_stored_value(self):
        # An imported "Lounas" is shown in the Lunch section; saving the entry
        # without changing its meal does not rewrite the stored name.
        MealEntry.objects.filter(pk=self.entry.pk).update(meal_type="Lounas")
        self.entry.refresh_from_db()
        self.assertContains(self.client.get(self.url), 'value="Lounas"')
        self._post(meal_type="Lounas", food_name="Banaani")
        self.assertEqual(self.entry.meal_type, "Lounas")


class FreeMealNamesTests(TestCase):
    """The user chooses the meal: the diary shows only the meals logged that
    day, and any own meal name can be used. Stored names are never rewritten."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="freemeals", password="x")
        self.client.force_login(self.user)
        FineliFood.objects.create(fineli_id=11049, name_fi="BANAANI, KUORITTU", energy_kcal=90, protein_g=1.2)

    def _add(self, meal_type, date="2026-10-12"):
        import json as _json
        return self.client.post(reverse("add_fineli_entry"), data=_json.dumps({
            "date": date, "fineli_id": 11049, "name": "BANAANI, KUORITTU", "meal_type": meal_type,
            "portion_grams": 100,
        }), content_type="application/json")

    def _diary(self, date="2026-10-12"):
        return self.client.get(reverse("food_diary"), {"date": date})

    def test_empty_day_has_no_meal_sections(self):
        resp = self._diary()
        self.assertEqual(resp.context["meal_sections"], [])
        self.assertContains(resp, "Nothing logged for this day yet.")

    def test_only_logged_meals_are_shown_in_logging_order(self):
        self._add("Ennen kisaa")
        self._add("breakfast")
        self._add("Ennen kisaa")
        sections = self._diary().context["meal_sections"]
        self.assertEqual([s["key"] for s in sections], ["Ennen kisaa", "breakfast"])
        self.assertEqual([s["name"] for s in sections], ["Ennen kisaa", "Breakfast"])
        self.assertEqual(len(sections[0]["entries"]), 2)
        self.assertEqual(sections[0]["totals"]["calories"], 180)

    def test_own_meal_name_is_stored_as_typed(self):
        resp = self._add("  Kisa   geelit ")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(MealEntry.objects.get().meal_type, "Kisa geelit")

    def test_standard_meal_name_is_stored_as_key_in_both_languages(self):
        for name in ("Aamiainen", "Lunch", "välipala", "Päivällinen", "Supper"):
            self._add(name)
        self.assertEqual(
            sorted(MealEntry.objects.values_list("meal_type", flat=True)),
            ["breakfast", "dinner", "evening_snack", "lunch", "snack"],
        )

    def test_meal_is_required(self):
        resp = self._add("  ")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(MealEntry.objects.exists())

    def test_too_long_meal_name_is_rejected(self):
        resp = self._add("x" * 51)
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(MealEntry.objects.exists())

    def test_manual_entry_uses_chosen_meal(self):
        resp = self.client.post(reverse("add_meal_entry"), {
            "date": "2026-10-12", "meal_type": "Yöpala", "food_name": "Leipä", "portion_grams": "50",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(MealEntry.objects.get().meal_type, "Yöpala")
        resp = self.client.post(reverse("add_meal_entry"), {"date": "2026-10-12", "meal_type": "", "food_name": "X"})
        self.assertEqual(resp.status_code, 400)

    def test_imported_standard_name_shares_section_without_rewrite(self):
        # A diary CSV import may store a standard meal's name verbatim; it is
        # shown in that meal's section and the stored value stays as it was.
        self._add("lunch")
        self._add("snack")
        MealEntry.objects.filter(meal_type="snack").update(meal_type="Lounas")
        sections = self._diary().context["meal_sections"]
        self.assertEqual([s["key"] for s in sections], ["lunch"])
        self.assertEqual(len(sections[0]["entries"]), 2)
        self.assertTrue(MealEntry.objects.filter(meal_type="Lounas").exists())

    def test_meal_suggestions_are_the_five_standard_meals(self):
        self._add("Herkku", date="2026-10-01")
        resp = self._diary()
        self.assertEqual(resp.context["meal_options"], ["Breakfast", "Lunch", "Snack", "Dinner", "Supper"])
        self.assertContains(resp, 'role="option"', count=5)
        self.assertContains(resp, 'id="mealName"')
        # The meal is added on the page; the add-food window has no meal field.
        self.assertContains(resp, 'id="newMealSection"')
        self.assertContains(resp, 'id="mealName"', count=1)

    def test_delete_meal_removes_only_that_meals_own_entries(self):
        self._add("lunch")
        self._add("lunch", date="2026-10-13")
        self._add("Ennen kisaa")
        self._add("snack")
        MealEntry.objects.filter(meal_type="snack").update(meal_type="Lounas")  # imported name, Lunch section
        other = get_user_model().objects.create_user(username="freemealsdel", password="x")
        log = DailyLog.objects.get(date="2026-10-12")
        MealEntry.objects.create(daily_log=log, user=other, food_name="X", meal_type="lunch", portion_grams=1)

        resp = self.client.post(reverse("delete_meal"), {"date": "2026-10-12", "meal": "lunch"})
        self.assertEqual(resp.json(), {"deleted": 2})
        mine = MealEntry.objects.filter(user=self.user)
        self.assertEqual(
            sorted((str(e.daily_log.date), e.meal_type) for e in mine),
            [("2026-10-12", "Ennen kisaa"), ("2026-10-13", "lunch")],
        )
        self.assertTrue(MealEntry.objects.filter(user=other).exists())
        log.refresh_from_db()
        self.assertEqual(log.calories_in, 90)  # the remaining "Ennen kisaa" banana

    def test_delete_meal_needs_post(self):
        self._add("lunch")
        self.assertEqual(self.client.get(reverse("delete_meal"), {"date": "2026-10-12", "meal": "lunch"}).status_code, 400)
        self.assertEqual(MealEntry.objects.count(), 1)

    def test_meal_sections_have_delete_buttons(self):
        self._add("Ennen kisaa")
        resp = self._diary()
        self.assertContains(resp, 'data-meal-key="Ennen kisaa"')
        self.assertContains(resp, 'onclick="deleteMeal(this)"', count=2)  # the section and the new-meal template

    def test_finnish_meal_suggestions(self):
        from django.utils import translation
        with translation.override("fi"):
            from .meals import meal_options
            self.assertEqual(meal_options(), ["Aamiainen", "Lounas", "Välipala", "Päivällinen", "Illallinen"])


class ServerSideEntryValuesTests(TestCase):
    """Diary entries take per-100 g values from the database, not from the
    browser's (possibly stale) copy; unknown Fineli ids are rejected."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="serverside", password="x")
        self.client.force_login(self.user)
        self.oats = FineliFood.objects.create(fineli_id=153, name_fi="KAURAHIUTALE", energy_kcal=380, protein_g=13)
        self.quark = CustomFood.objects.create(user=self.user, name="Rahka", brand="Valio",
                                               energy_kcal_per_100g=60, protein_per_100g=10)

    def _post(self, **payload):
        import json as _json
        data = {"date": "2026-10-12", "meal_type": "snack", "portion_grams": 200}
        data.update(payload)
        return self.client.post(reverse("add_fineli_entry"), data=_json.dumps(data), content_type="application/json")

    def test_fineli_values_come_from_database(self):
        self._post(fineli_id=153, name="Fake", energy_kcal=9999, protein=0)
        entry = MealEntry.objects.get()
        self.assertEqual((entry.food_name, entry.calories, entry.protein_g), ("KAURAHIUTALE", 760, 26))

    def test_custom_values_come_from_database(self):
        # The browser still had old values (e.g. opened before an edit).
        self._post(source="custom", custom_food_id=self.quark.id, name="Rahka", energy_kcal=50, protein=8)
        entry = MealEntry.objects.get()
        self.assertEqual((entry.food_name, entry.calories, entry.protein_g), ("Rahka (Valio)", 120, 20))

    def test_unknown_fineli_id_rejected_and_not_created(self):
        resp = self._post(fineli_id=424242, name="Injected", energy_kcal=1)
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(FineliFood.objects.filter(fineli_id=424242).exists())
        self.assertFalse(MealEntry.objects.exists())


class DependentRecipeRecalculationTests(TestCase):
    """When a food changes, every recipe using it - also through another
    recipe - is recalculated, ingredients before the recipes using them."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="dependents", password="x")
        self.client.force_login(self.user)
        self.oats = FineliFood.objects.create(fineli_id=153, name_fi="KAURAHIUTALE", energy_kcal=400)
        self.quark = CustomFood.objects.create(user=self.user, name="Rahka", energy_kcal_per_100g=60)
        # Puuro = 100 g oats + 100 g quark; Aamiainen = 100 g Puuro + 100 g quark
        self.porridge = CustomFood.objects.create(user=self.user, name="Puuro", kind=CustomFood.KIND_RECIPE)
        CustomFoodIngredient.objects.create(custom_food=self.porridge, fineli_food=self.oats, amount_grams=100)
        CustomFoodIngredient.objects.create(custom_food=self.porridge, source_custom_food=self.quark, amount_grams=100)
        self.breakfast = CustomFood.objects.create(user=self.user, name="Aamiainen", kind=CustomFood.KIND_RECIPE)
        CustomFoodIngredient.objects.create(custom_food=self.breakfast, source_custom_food=self.porridge, amount_grams=100)
        CustomFoodIngredient.objects.create(custom_food=self.breakfast, source_custom_food=self.quark, amount_grams=100)
        self.client.post(reverse("recalculate_recipe", args=[self.breakfast.id]))
        self._refresh()

    def _refresh(self):
        for food in (self.quark, self.porridge, self.breakfast):
            food.refresh_from_db()

    def test_starting_values(self):
        self.assertAlmostEqual(self.porridge.energy_kcal_per_100g, 230)    # (400 + 60) / 2
        self.assertAlmostEqual(self.breakfast.energy_kcal_per_100g, 145)   # (230 + 60) / 2

    def test_editing_a_product_updates_recipes_using_it(self):
        resp = self.client.post(reverse("edit_custom_food", args=[self.quark.id]), {
            "name": "Rahka", "brand": "", "energy_kcal_per_100g": "100",
            "package_amount": "", "package_unit": "G", "nutrition_basis": "G", "default_portion_g": "100",
        }, follow=True)
        self._refresh()
        self.assertAlmostEqual(self.porridge.energy_kcal_per_100g, 250)    # (400 + 100) / 2
        self.assertAlmostEqual(self.breakfast.energy_kcal_per_100g, 175)   # (250 + 100) / 2, uses fresh Puuro
        self.assertIn("Also recalculated the recipes that use Rahka: Puuro, Aamiainen.",
                      [str(m) for m in resp.context["messages"]])

    def test_changing_a_recipe_updates_recipes_using_it(self):
        self.client.post(reverse("add_recipe_fineli_ingredient", args=[self.porridge.id]),
                         {"fineli_id": 153, "amount": "100", "unit": "G"})
        self._refresh()
        self.assertAlmostEqual(self.porridge.energy_kcal_per_100g, (800 + 60) / 3)
        self.assertAlmostEqual(self.breakfast.energy_kcal_per_100g, ((800 + 60) / 3 + 60) / 2)

    def test_manual_final_values_reach_recipes_using_it(self):
        self.client.post(reverse("save_recipe_final_values", args=[self.porridge.id]), {"energy_kcal_per_100g": "300"})
        self._refresh()
        self.assertAlmostEqual(self.breakfast.energy_kcal_per_100g, 180)   # (300 + 60) / 2

    def test_unrelated_recipes_untouched(self):
        other = CustomFood.objects.create(user=self.user, name="Muu", kind=CustomFood.KIND_RECIPE,
                                          energy_kcal_per_100g=999)
        CustomFoodIngredient.objects.create(custom_food=other, fineli_food=self.oats, amount_grams=10)
        CustomFood.objects.filter(pk=other.pk).update(energy_kcal_per_100g=999)
        from .recipe_persistence import recalculate_dependent_recipes
        self.assertEqual([r.name for r in recalculate_dependent_recipes(self.quark)], ["Puuro", "Aamiainen"])
        other.refresh_from_db()
        self.assertEqual(other.energy_kcal_per_100g, 999)


class RecipePageMobileFlowTests(TestCase):
    """Recipe page on a phone: every action returns to where it was made
    (with the search kept), rarely used sections are folded, ingredients
    show their own kcal/carbs/protein, and nutrition can be shown per 100 g,
    per serving or for the whole recipe."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="recipemobile", password="x")
        self.client.force_login(self.user)
        self.oats = FineliFood.objects.create(fineli_id=153, name_fi="KAURAHIUTALE", energy_kcal=380,
                                              carbs_g=60, protein_g=13)
        self.berry = FineliFood.objects.create(fineli_id=7, name_fi="MUSTIKKA", energy_kcal=40, carbs_g=None, protein_g=0.5)
        self.recipe = CustomFood.objects.create(user=self.user, name="Puuro", kind=CustomFood.KIND_RECIPE)
        self.url = reverse("recipe_detail", args=[self.recipe.id])

    def _add_oats(self, **extra):
        data = {"fineli_id": 153, "amount": "50", "unit": "G"}
        data.update(extra)
        return self.client.post(reverse("add_recipe_fineli_ingredient", args=[self.recipe.id]), data)

    # --- A: back to the right place ---
    def test_adding_from_fineli_returns_to_fineli_search_with_query(self):
        resp = self._add_oats(q="kaura")
        self.assertRedirects(resp, self.url + "?q=kaura#add-fineli", fetch_redirect_response=False)

    def test_adding_own_food_returns_to_my_foods_with_search(self):
        quark = CustomFood.objects.create(user=self.user, name="Rahka", energy_kcal_per_100g=60)
        resp = self.client.post(reverse("add_recipe_custom_ingredient", args=[self.recipe.id]),
                                {"source_custom_food_id": quark.id, "amount_grams": "100", "own": "rah"})
        self.assertRedirects(resp, self.url + "?own=rah#add-own", fetch_redirect_response=False)
        page = self.client.get(self.url, {"own": "rah"})
        self.assertContains(page, 'id="ownFoodSearch" value="rah"')

    def test_removing_returns_to_ingredients(self):
        self._add_oats()
        ing = self.recipe.ingredients.get()
        resp = self.client.post(reverse("remove_recipe_ingredient", args=[self.recipe.id, ing.id]))
        self.assertRedirects(resp, self.url + "#ingredients", fetch_redirect_response=False)

    def test_saves_return_to_their_section(self):
        resp = self.client.post(reverse("update_recipe_settings", args=[self.recipe.id]),
                                {"name": "Puuro", "brand": "", "servings": "2", "cooked_weight_g": ""})
        self.assertRedirects(resp, self.url + "#recipe-details", fetch_redirect_response=False)
        resp = self.client.post(reverse("save_recipe_final_values", args=[self.recipe.id]), {"energy_kcal_per_100g": "100"})
        self.assertRedirects(resp, self.url + "#final-values", fetch_redirect_response=False)

    def test_page_remembers_searches_in_add_forms(self):
        CustomFood.objects.create(user=self.user, name="Rahka", energy_kcal_per_100g=60)  # My Foods needs a food
        resp = self.client.get(self.url)
        self.assertContains(resp, '<input type="hidden" name="q" data-keep="fineliSearchInput">', html=True)
        self.assertContains(resp, '<input type="hidden" name="own" data-keep="ownFoodSearch">', html=True)

    # --- B: folded sections, adding near the top ---
    def test_rarely_used_sections_are_folded(self):
        content = self.client.get(self.url).content.decode()
        self.assertIn('<details class="section fold" id="recipe-details">', content)
        self.assertIn('<details class="subfold">', content)  # other nutrients and corrections
        order = [content.index(f'id="{i}"') for i in ("ingredients", "add-fineli", "add-own", "recipe-details", "final-values")]
        self.assertEqual(order, sorted(order))

    def test_folded_section_opens_on_form_errors(self):
        resp = self.client.post(reverse("update_recipe_settings", args=[self.recipe.id]),
                                {"name": "", "brand": "", "servings": "", "cooked_weight_g": ""})
        self.assertContains(resp, '<details class="section fold" id="recipe-details" open>')

    # --- per-ingredient values ---
    def test_ingredient_rows_show_kcal_carbs_protein(self):
        self._add_oats()
        self.client.post(reverse("add_recipe_fineli_ingredient", args=[self.recipe.id]),
                         {"fineli_id": 7, "amount": "100", "unit": "G"})
        resp = self.client.get(self.url)
        rows = resp.context["ingredient_rows"]
        self.assertEqual([(r["name"], r["kcal"], r["carbs"], r["protein"]) for r in rows],
                         [("KAURAHIUTALE", 190, 30, 6.5), ("MUSTIKKA", 40, None, 0.5)])
        self.assertEqual(resp.context["ingredient_totals"], {"kcal": 230, "carbs": 30, "protein": 7.0})
        self.assertTrue(resp.context["ingredient_partial"]["carbs"])
        self.assertContains(resp, "<span>190 kcal</span>", html=True)
        self.assertContains(resp, "C &ge; 30.0 g")  # berries' carbs unknown -> at least

    def test_remove_needs_a_second_tap(self):
        self._add_oats()
        self.assertContains(self.client.get(self.url), 'data-confirm="Remove?"')

    # --- nutrition per 100 g / serving / whole recipe ---
    def test_basis_switch_with_servings(self):
        self._add_oats()  # 50 g of oats -> 380 kcal / 100 g
        self.client.post(reverse("update_recipe_settings", args=[self.recipe.id]),
                         {"name": "Puuro", "brand": "", "servings": "2", "cooked_weight_g": ""})
        content = self.client.get(self.url).content.decode()
        self.assertIn('data-basis="100" data-factor="100"', content)
        self.assertIn('data-basis="serving" data-factor="25.000000"', content)
        self.assertIn('data-basis="total" data-factor="50.000000"', content)
        self.assertIn('data-per100="380.0" data-unit="kcal">380 kcal', content)

    def test_serving_disabled_without_servings(self):
        self._add_oats()
        resp = self.client.get(self.url)
        self.assertContains(resp, 'disabled title="Set the servings under Recipe details"')
        self.assertContains(resp, "to see values per serving")

    def test_unknown_values_stay_unknown(self):
        self.client.post(reverse("add_recipe_fineli_ingredient", args=[self.recipe.id]),
                         {"fineli_id": 7, "amount": "100", "unit": "G"})
        self.assertContains(self.client.get(self.url), 'data-per100="" data-unit="g"><span class="unknown">unknown</span>')


class LanguageSettingsTests(TestCase):
    """Settings choose the app language (English / Suomi); in English the
    Fineli food names can be Finnish or English, in Finnish they are always
    Finnish. Portion units follow the app language. Nothing stored changes."""

    def setUp(self):
        from .models import FineliFoodUnit
        self.user = get_user_model().objects.create_user(username="languser", password="x")
        self.client.force_login(self.user)
        self.oats = FineliFood.objects.create(fineli_id=153, name_fi="KAURAHIUTALE", name_en="OATS, ROLLED, OAT FLAKES",
                                              energy_kcal=380)
        FineliFoodUnit.objects.create(fineli_food=self.oats, unit_code="DL", mass_g=35)
        FineliFoodUnit.objects.create(fineli_food=self.oats, unit_code="KPL_L", mass_g=50)

    def _settings(self, **values):
        from core.models import UserSettings
        UserSettings.objects.update_or_create(pk=1, defaults=values)

    def _search(self, q="kaura"):
        return self.client.get(reverse("search_fineli_api"), {"q": q}).json()["results"][0]

    def test_default_is_english_app_with_finnish_food_names(self):
        food = self._search()
        self.assertEqual(food["name"], "KAURAHIUTALE")
        self.assertEqual([u["label"] for u in food["units"]], ["large piece", "dl"])

    def test_english_food_names_when_chosen(self):
        self._settings(language="en", food_language="en")
        food = self._search("oat flakes")
        self.assertEqual(food["name"], "Oats, rolled, oat flakes")

    def test_finnish_app_always_uses_finnish_names_and_units(self):
        self._settings(language="fi", food_language="en")
        food = self._search()
        self.assertEqual(food["name"], "KAURAHIUTALE")
        self.assertEqual([u["label"] for u in food["units"]], ["iso (kpl)", "dl"])

    def test_finnish_app_translates_django_texts_and_sets_language_header(self):
        self._settings(language="fi")
        resp = self.client.get(reverse("food_diary"))
        self.assertEqual(resp.headers["Content-Language"], "fi")

    def test_diary_shows_logged_and_imported_fineli_entries_in_english(self):
        import json as _json
        self.client.post(reverse("add_fineli_entry"), data=_json.dumps({
            "date": "2026-10-12", "fineli_id": 153, "meal_type": "breakfast", "amount": 1, "unit": "DL",
        }), content_type="application/json")
        log = DailyLog.objects.get(date="2026-10-12")
        MealEntry.objects.create(daily_log=log, user=self.user, source_type="bulk_import", food_name="Kaurahiutale",
                                 meal_type="breakfast", portion_grams=40, calories=152, source_food_id=153)
        self.assertEqual(MealEntry.objects.filter(food_name="KAURAHIUTALE").count(), 1)  # stored as logged
        self._settings(language="en", food_language="en")
        resp = self.client.get(reverse("food_diary"), {"date": "2026-10-12"})
        self.assertEqual([e.shown_name for e in resp.context["meal_entries"]], ["Oats, rolled, oat flakes"] * 2)
        self.assertContains(resp, "1 × dl")

    def test_recipe_pickers_and_ingredients_use_food_language(self):
        self._settings(language="en", food_language="en")
        recipe = CustomFood.objects.create(user=self.user, name="Puuro", kind=CustomFood.KIND_RECIPE)
        CustomFoodIngredient.objects.create(custom_food=recipe, fineli_food=self.oats, amount_grams=40)
        resp = self.client.get(reverse("recipe_detail", args=[recipe.id]), {"q": "oat"})
        self.assertEqual(resp.context["ingredient_rows"][0]["name"], "Oats, rolled, oat flakes")
        self.assertContains(resp, 'data-name="Oats, rolled, oat flakes"')

    def test_settings_page_saves_languages(self):
        from core.models import UserSettings
        resp = self.client.get(reverse("settings"))
        self.assertContains(resp, 'name="language"')
        self.assertContains(resp, 'name="food_language"')
        s = UserSettings.get_settings()
        data = {
            "weight_kg": "70.5", "height_cm": "170", "age": "40", "sex": "F", "activity_level": "1.725",
            "fitness_goal": "maintain", "custom_goal_kcal": "", "max_daily_deficit": "100",
            "garmin_calorie_adjustment": "0", "min_daily_calories": "1500", "max_daily_calories": "3000",
            "protein_goal_g": "100", "carbs_goal_g": "250", "fat_goal_g": "70", "week_start_day": "0",
            "language": "fi", "food_language": "en",
        }
        self.client.post(reverse("settings"), data)
        s.refresh_from_db()
        self.assertEqual((s.language, s.food_language, s.effective_food_language()), ("fi", "en", "fi"))

    def test_finnish_numbers_stay_machine_readable_in_inputs_and_scripts(self):
        self._settings(language="fi", weight_kg=70.5, height_cm=170, age=40, sex="F")
        resp = self.client.get(reverse("settings"))
        self.assertContains(resp, 'value="70.5"')
        self.assertNotContains(resp, 'value="70,5"')
        self.assertRegex(resp.content.decode(), r"const TARGET_CALORIES = [0-9.]+;")


class FoodDiaryDayMicronutrientTests(TestCase):
    """The diary's micronutrient panel: the user's own entries only, an
    unknown value is not zero (coverage x/y), and nutrients no food reports
    are left out."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="micros", password="x")
        self.client.force_login(self.user)
        self.log = DailyLog.objects.create(date="2026-10-12")

    def _entry(self, user=None, **nutrients):
        entry = MealEntry.objects.create(
            daily_log=self.log, user=user or self.user, source_type="manual", food_name="Food",
            meal_type="lunch", portion_grams=100, calories=100,
        )
        for key, value in nutrients.items():
            MealEntryNutrient.objects.create(meal_entry=entry, nutrient_key=key, value=value, unit=NUTRIENT_UNITS[key])
        return entry

    def _panel(self):
        resp = self.client.get(reverse("food_diary"), {"date": "2026-10-12"})
        return resp, {row["label"]: row for group in resp.context["day_micronutrients"] for row in group["rows"]}

    def test_totals_with_coverage(self):
        self._entry(iron=2.5, vitamin_d=1.2)
        self._entry(iron=4.0)
        self._entry()
        resp, rows = self._panel()
        self.assertEqual(rows["Iron"]["amount"], "6.5")
        self.assertEqual(rows["Iron"]["unit"], "mg")
        self.assertEqual((rows["Iron"]["known"], rows["Iron"]["partial"]), (2, True))
        self.assertEqual(rows["Vitamin D"]["unit"], "µg")
        self.assertNotIn("Calcium", rows)
        self.assertContains(resp, "2/3")

    def test_complete_coverage_is_not_partial(self):
        self._entry(calcium=120)
        self._entry(calcium=300)
        _, rows = self._panel()
        self.assertEqual(rows["Calcium"]["amount"], "420")
        self.assertFalse(rows["Calcium"]["partial"])

    def test_other_users_entries_are_not_counted(self):
        other = get_user_model().objects.create_user(username="other", password="x")
        self._entry(iron=1.0)
        self._entry(user=other, iron=50.0)
        _, rows = self._panel()
        self.assertEqual(rows["Iron"]["amount"], "1.0")
        self.assertFalse(rows["Iron"]["partial"])

    def test_no_panel_without_micronutrients(self):
        self._entry()
        resp, rows = self._panel()
        self.assertEqual(rows, {})
        self.assertNotContains(resp, "<details class=\"micro-panel\">")


class MicronutrientReferenceTests(TestCase):
    """Day micronutrients as a % of the adult reference intake (Finnish
    recommendations 2024 / NNR2023), by the sex and age in Settings."""

    def setUp(self):
        from core.models import UserSettings
        self.user = get_user_model().objects.create_user(username="microref", password="x")
        self.client.force_login(self.user)
        self.log = DailyLog.objects.create(date="2026-10-12")
        self.settings = UserSettings.get_settings()

    def _set(self, sex, age=None):
        self.settings.sex, self.settings.age = sex, age
        self.settings.save()

    def _entry(self, calories=100, **nutrients):
        entry = MealEntry.objects.create(
            daily_log=self.log, user=self.user, source_type="manual", food_name="Food",
            meal_type="lunch", portion_grams=100, calories=calories,
        )
        for key, value in nutrients.items():
            MealEntryNutrient.objects.create(meal_entry=entry, nutrient_key=key, value=value, unit=NUTRIENT_UNITS[key])

    def _panel(self):
        resp = self.client.get(reverse("food_diary"), {"date": "2026-10-12"})
        return resp, {row["label"]: row["reference"] for group in resp.context["day_micronutrients"] for row in group["rows"]}

    def test_no_percentages_without_sex(self):
        self._set(None)
        self._entry(iron=4.5)
        resp, refs = self._panel()
        self.assertIsNone(refs["Iron"])
        self.assertContains(resp, "Set your sex in")

    def test_men_and_women_have_own_values(self):
        self._entry(iron=4.5, vitamin_c=55)
        self._set("M", 35)
        _, refs = self._panel()
        self.assertEqual((refs["Iron"], refs["Vitamin C"]), ((50, False), (50, False)))  # 9 mg, 110 mg
        self._set("F", 35)
        _, refs = self._panel()
        self.assertEqual(refs["Iron"], (30, False))  # 15 mg

    def test_age_groups(self):
        self._entry(iron=4, calcium=500, vitamin_d=10)
        self._set("F", 60)
        _, refs = self._panel()
        self.assertEqual(refs["Iron"], (50, False))  # 8 mg at 51-70
        self.assertEqual(refs["Vitamin D"], (100, False))  # 10 µg
        self._set("F", 20)
        _, refs = self._panel()
        self.assertEqual(refs["Calcium"], (50, False))  # 1000 mg at 18-24
        self._set("F", 80)
        _, refs = self._panel()
        self.assertEqual(refs["Vitamin D"], (50, False))  # 20 µg from 75

    def test_per_mj_nutrients_follow_the_days_energy(self):
        self._set("M", 35)
        self._entry(calories=2390.06, thiamine=0.5)  # 10 MJ -> 1.0 mg
        _, refs = self._panel()
        self.assertEqual(refs["Thiamine"], (50, False))

    def test_sodium_against_its_maximum(self):
        self._set("F", 35)
        self._entry(sodium=984.25)  # half of 5 g salt
        resp, refs = self._panel()
        self.assertEqual(refs["Sodium"], (50, True))
        self.assertContains(resp, "50 % of max")

    def test_no_reference_for_fats(self):
        self._set("M", 35)
        self._entry(omega3=1.5, cholesterol=200)
        _, refs = self._panel()
        self.assertEqual(set(refs.values()), {None})

    def test_reference_without_age_uses_25_to_50(self):
        from .reference_intakes import reference_intake
        self.assertEqual(reference_intake("calcium", "F"), (950, False))
        self.assertEqual(reference_intake("zinc", "M", 80), (12.1, False))
        self.assertIsNone(reference_intake("iron", None, 30))
