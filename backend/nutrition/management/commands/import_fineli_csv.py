"""
Management command to import COMPLETE Fineli food database from official CSV file

This downloads and imports ALL 4,200+ foods from Fineli's official CSV dataset.
This is the ONLY way to get the complete database - the API search endpoint
cannot return all foods without knowing exact search terms.

CSV Download: https://fineli.fi/fineli/en/avoin-data
Direct link: https://fineli.fi/fineli/api/v1/downloads/component-value-file

Usage:
    python manage.py import_fineli_csv
    python manage.py import_fineli_csv --csv-path /path/to/component-value.csv
    python manage.py import_fineli_csv --update-existing
"""

from django.core.management.base import BaseCommand
import csv
import os
from nutrition.models import FineliFood, FineliFoodNutrient, FineliFoodUnit, NUTRIENT_UNITS

# Existing supported auxiliary nutrient_key -> official Fineli EUFDNAME
# component code. Verified against the local Release 20 catalog
# (component.csv / eufdname_FI.csv): every code's declared unit already
# matches the canonical NUTRIENT_UNITS unit for that key, so no conversion
# is needed here (unlike core salt, which is NACL in mg -> salt_g in g).
AUX_COMPONENT_CODES = {
    'alcohol': 'ALC', 'organic_acids': 'OA', 'sugar_alcohols': 'SUGOH',
    'fructose': 'FRUS', 'galactose': 'GALS', 'glucose': 'GLUS',
    'lactose': 'LACS', 'maltose': 'MALS', 'sucrose': 'SUCS',
    'starch': 'STARCH', 'insoluble_fiber': 'FIBINS',
    'soluble_noncellulose_polysaccharide': 'PSACNCS',
    'fatty_acids_total': 'FAFRE', 'pufa': 'FAPU', 'mufa_cis': 'FAMCIS',
    'trans_fat': 'FATRN', 'omega3': 'FAPUN3', 'omega6': 'FAPUN6',
    'linoleic_acid': 'F18D2CN6', 'ala': 'F18D3N3', 'epa': 'F20D5N3',
    'dha': 'F22D6N3', 'cholesterol': 'CHOLE', 'sterols': 'STERT',
    'calcium': 'CA', 'iron': 'FE', 'iodine': 'ID', 'potassium': 'K',
    'magnesium': 'MG', 'sodium': 'NA', 'phosphorus': 'P', 'selenium': 'SE',
    'zinc': 'ZN', 'tryptophan': 'TRP', 'folate': 'FOL',
    'niacin_equivalent': 'NIAEQ', 'niacin': 'NIA', 'vitamin_b6': 'VITPYRID',
    'riboflavin': 'RIBF', 'thiamine': 'THIA', 'vitamin_a_rae': 'VITA',
    'carotenoids': 'CAROTENS', 'vitamin_b12': 'VITB12', 'vitamin_c': 'VITC',
    'vitamin_d': 'VITD', 'vitamin_e': 'VITE', 'vitamin_k': 'VITK',
}


class Command(BaseCommand):
    help = '''Import COMPLETE Fineli food database from official CSV file
    
    This downloads all 4,200+ foods from Fineli's official CSV dataset.
    The CSV contains the complete database, unlike the search API which
    requires guessing search terms and will always miss foods.
    
    Attribution: Finnish Institute for Health and Welfare (THL), Fineli®
    License: Creative Commons 4.0 Attribution (CC-BY)
    '''

    def add_arguments(self, parser):
        parser.add_argument(
            '--data-folder',
            type=str,
            default=None,
            help='Path to folder containing Fineli CSV files (foodname_FI.csv, component_value.csv, etc.)'
        )
        parser.add_argument(
            '--csv-path',
            type=str,
            default=None,
            help='DEPRECATED: Use --data-folder instead'
        )
        parser.add_argument(
            '--update-existing',
            action='store_true',
            help='Update existing foods in database'
        )

    def handle(self, *args, **options):
        data_folder = options['data_folder']
        update_existing = options['update_existing']
        
        if not data_folder:
            self.stdout.write(self.style.ERROR('Error: --data-folder is required'))
            self.stdout.write('Usage: python manage.py import_fineli_csv --data-folder "path/to/Fineli_Rel20"')
            return
        
        if not os.path.exists(data_folder):
            self.stdout.write(self.style.ERROR(f'Folder not found: {data_folder}'))
            return
        
        self.stdout.write(self.style.WARNING('Starting Fineli CSV import...'))
        self.stdout.write(f'Data folder: {data_folder}')
        
        # Step 1: Read food names
        self.stdout.write('Reading food names...')
        food_names = {}  # {food_id: {name_fi, name_sv, name_en}}
        
        for lang, key in [('FI', 'name_fi'), ('SV', 'name_sv'), ('EN', 'name_en')]:
            filename = os.path.join(data_folder, f'foodname_{lang}.csv')
            if not os.path.exists(filename):
                self.stdout.write(self.style.WARNING(f'  File not found: foodname_{lang}.csv'))
                continue
            
            with open(filename, 'r', encoding='latin-1') as f:
                reader = csv.DictReader(f, delimiter=';')
                for row in reader:
                    food_id = row.get('FOODID')
                    food_name = row.get('FOODNAME', '')
                    if food_id:
                        if food_id not in food_names:
                            food_names[food_id] = {}
                        food_names[food_id][key] = food_name
        
        self.stdout.write(f'Found names for {len(food_names)} foods')
        
        # Step 2: Read nutrient values
        self.stdout.write('Reading nutrient values...')
        component_file = os.path.join(data_folder, 'component_value.csv')
        if not os.path.exists(component_file):
            self.stdout.write(self.style.ERROR(f'File not found: component_value.csv'))
            return
        
        nutrients_by_food = {}  # {food_id: {component_code: value}}
        with open(component_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f, delimiter=';')
            for row in reader:
                food_id = row.get('FOODID')
                component = row.get('EUFDNAME')
                value_str = row.get('BESTLOC', '')
                
                if food_id and component and value_str:
                    if food_id not in nutrients_by_food:
                        nutrients_by_food[food_id] = {}
                    try:
                        value = float(value_str.replace(',', '.'))
                        nutrients_by_food[food_id][component] = value
                    except ValueError:
                        pass
        
        self.stdout.write(f'Found nutrients for {len(nutrients_by_food)} foods')

        # Step 2b: Read portion units (optional file - older packages may
        # lack it; then foods simply get no units).
        units_by_food = {}  # {food_id: {unit_code: mass_g}}
        unit_file = os.path.join(data_folder, 'foodaddunit.csv')
        if os.path.exists(unit_file):
            with open(unit_file, 'r', encoding='utf-8-sig') as f:
                for row in csv.DictReader(f, delimiter=';'):
                    food_id, unit_code = row.get('FOODID'), row.get('FOODUNIT')
                    try:
                        mass = float((row.get('MASS') or '').replace(',', '.'))
                    except ValueError:
                        continue
                    if food_id and unit_code and mass > 0:
                        units_by_food.setdefault(food_id, {})[unit_code] = mass
            self.stdout.write(f'Found portion units for {len(units_by_food)} foods')
        else:
            self.stdout.write(self.style.WARNING('  File not found: foodaddunit.csv (no portion units imported)'))

        # Step 3: Combine and import
        self.stdout.write('Importing into database...')
        
        # Nutrient code mappings
        ENERGY_KCAL = 'ENERC'
        PROTEIN = 'PROT'
        FAT = 'FAT'
        CARBS = 'CHOAVL'
        SUGAR = 'SUGAR'
        FIBER = 'FIBC'
        SALT = 'NACL'  # Fineli "suola" (salt), reported in mg - not SALTEQ, which doesn't exist in the dataset
        SAT_FAT = 'FASAT'
        
        created = 0
        updated = 0
        skipped = 0
        errors = 0
        
        # Get all unique food IDs
        all_food_ids = set(food_names.keys()) | set(nutrients_by_food.keys())
        
        for food_id in all_food_ids:
            # Skip if no name
            if food_id not in food_names:
                skipped += 1
                continue
            
            names = food_names[food_id]
            nutrients = nutrients_by_food.get(food_id, {})

            # NACL is reported in mg; every other mapped component here is
            # already in the field's native unit (g), so only salt needs
            # conversion - matches the diary CSV importer's mg->g precedent.
            salt_mg = nutrients.get(SALT)
            salt_g = salt_mg / 1000.0 if salt_mg is not None else None

            # ENERC is reported in kJ (component.csv: ENERC;KJ) but the
            # field is kcal - same /4.184 conversion as the diary CSV importer.
            energy_kj = nutrients.get(ENERGY_KCAL)
            energy_kcal = energy_kj / 4.184 if energy_kj is not None else None

            try:
                food_obj, is_created = FineliFood.objects.update_or_create(
                    fineli_id=int(food_id),
                    defaults={
                        'name_fi': names.get('name_fi', ''),
                        'name_sv': names.get('name_sv', ''),
                        'name_en': names.get('name_en', ''),
                        # Missing component code for this food = not reported
                        # by Fineli (unknown) - do not fabricate 0.
                        'energy_kcal': energy_kcal,
                        'protein_g': nutrients.get(PROTEIN),
                        'fat_g': nutrients.get(FAT),
                        'carbs_g': nutrients.get(CARBS),
                        'sugar_g': nutrients.get(SUGAR),
                        'fiber_g': nutrients.get(FIBER),
                        'salt_g': salt_g,
                        'sat_fat_g': nutrients.get(SAT_FAT),
                    }
                )
                
                # Replace this food's auxiliary rows wholesale: simplest way
                # to keep re-import idempotent, apply corrected values, and
                # drop a nutrient that became missing/unknown in the source
                # (no stale child rows left behind).
                food_obj.nutrients.all().delete()
                aux_rows = [
                    FineliFoodNutrient(
                        fineli_food=food_obj, nutrient_key=key,
                        value=nutrients[code], unit=NUTRIENT_UNITS[key],
                    )
                    for key, code in AUX_COMPONENT_CODES.items()
                    if code in nutrients
                ]
                if aux_rows:
                    FineliFoodNutrient.objects.bulk_create(aux_rows)

                # Portion units: same wholesale replace as the aux rows.
                food_obj.units.all().delete()
                units = units_by_food.get(food_id, {})
                if units:
                    FineliFoodUnit.objects.bulk_create([
                        FineliFoodUnit(fineli_food=food_obj, unit_code=code, mass_g=mass)
                        for code, mass in units.items()
                    ])

                if is_created:
                    created += 1
                elif update_existing:
                    updated += 1

                if (created + updated) % 500 == 0:
                    self.stdout.write(f'Progress: {created + updated} foods processed...')
                    
            except Exception as e:
                errors += 1
                if errors <= 5:
                    self.stdout.write(self.style.WARNING(f'Error with food {food_id}: {e}'))
        
        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('='*60))
        self.stdout.write(self.style.SUCCESS(f'✅ Import complete!'))
        self.stdout.write(self.style.SUCCESS(f'✅ Created: {created} new foods'))
        if update_existing:
            self.stdout.write(self.style.SUCCESS(f'📝 Updated: {updated} existing foods'))
        if skipped:
            self.stdout.write(self.style.WARNING(f'⏭️  Skipped: {skipped} foods (no name)'))
        if errors:
            self.stdout.write(self.style.WARNING(f'⚠️  Errors: {errors} foods'))
        self.stdout.write(self.style.SUCCESS(f'📊 Total in database: {FineliFood.objects.count()}'))
        self.stdout.write(self.style.SUCCESS('='*60))
        self.stdout.write('')
        self.stdout.write('Attribution: Finnish Institute for Health and Welfare (THL), Fineli®')
        self.stdout.write('License: CC-BY 4.0')
