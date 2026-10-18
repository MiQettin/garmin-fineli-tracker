import csv
from pathlib import Path
from datetime import datetime

from django.core.management.base import BaseCommand
from core.models import DailyLog
from activities.models import Activity, ActivityLap


class Command(BaseCommand):
    """
    Django management command to import Garmin data from CSV files.
    
    This unified command imports both daily health statistics and individual activities
    from CSV files exported by the get_garmindata.py script. It creates or updates
    database records for all imported data.
    
    Usage:
        python manage.py import_garmin_csv
        python manage.py import_garmin_csv --csv path/to/garmin_daily.csv
        python manage.py import_garmin_csv --activities-csv path/to/garmin_activities.csv
    
    Default CSV Locations:
        - Daily stats: backend/data/garmin_daily.csv
        - Activities: backend/data/garmin_activities.csv
    
    Daily Stats Import:
        Imports daily summaries including calories out, steps, distance, activity minutes,
        and heart rate metrics (resting, min, max).
    
    Activities Import:
        Imports individual workout sessions (running, walking, dancing, strength) with
        detailed metrics like duration, distance, pace, heart rate, elevation, and VO2 max.
    
    Features:
        - Creates new records or updates existing ones (upsert)
        - Handles missing optional fields gracefully
        - Provides clear success/warning messages
        - Validates required CSV columns
    """
    
    help = "Import Garmin daily stats and activities from CSV files"

    def add_arguments(self, parser):
        """
        Define command-line arguments.
        
        Args:
            parser (ArgumentParser): Django command argument parser
        """
        parser.add_argument(
            "--csv",
            default=str(Path(__file__).resolve().parents[4] / "backend" / "data" / "garmin_daily.csv"),
            help="Path to Garmin daily CSV (default: backend/data/garmin_daily.csv)",
        )
        parser.add_argument(
            "--activities-csv",
            default=str(Path(__file__).resolve().parents[4] / "backend" / "data" / "garmin_activities.csv"),
            help="Path to Garmin activities CSV (default: backend/data/garmin_activities.csv)",
        )
        parser.add_argument(
            "--laps-csv",
            default=str(Path(__file__).resolve().parents[4] / "backend" / "data" / "garmin_activity_laps.csv"),
            help="Path to Garmin activity laps CSV (default: backend/data/garmin_activity_laps.csv)",
        )

    def handle(self, *args, **options):
        """
        Execute the import process.
        
        Process:
            1. Import daily health statistics from garmin_daily.csv
            2. Import individual activities from garmin_activities.csv
            3. Report success/failure for each step
        
        Args:
            *args: Positional arguments (unused)
            **options: Command options including --csv and --activities-csv paths
        
        Raises:
            FileNotFoundError: If daily stats CSV is not found
            ValueError: If required CSV columns are missing
        """
        # Import daily stats
        csv_path = Path(options["csv"]).resolve()

        if not csv_path.exists():
            raise FileNotFoundError(f"CSV not found: {csv_path}")

        with csv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            required = {"date", "calories_out"}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError(f"CSV must contain columns: {sorted(required)}")

            count = 0
            for row in reader:
                date_str = row["date"]
                calories_out = int(float(row["calories_out"]))
                
                # Parse optional activity and HR fields
                defaults = {"calories_out": calories_out}

                # Active/BMR calorie split (components of calories_out)
                if "active_kcal" in row and row["active_kcal"]:
                    defaults["active_kcal"] = int(float(row["active_kcal"]))
                if "bmr_kcal" in row and row["bmr_kcal"]:
                    defaults["bmr_kcal"] = int(float(row["bmr_kcal"]))

                # Activity data
                if "steps" in row and row["steps"]:
                    defaults["steps"] = int(float(row["steps"]))
                if "distance_meters" in row and row["distance_meters"]:
                    defaults["distance_meters"] = int(float(row["distance_meters"]))
                if "moderate_intensity_minutes" in row and row["moderate_intensity_minutes"]:
                    defaults["moderate_intensity_minutes"] = int(float(row["moderate_intensity_minutes"]))
                if "vigorous_intensity_minutes" in row and row["vigorous_intensity_minutes"]:
                    defaults["vigorous_intensity_minutes"] = int(float(row["vigorous_intensity_minutes"]))
                
                # Heart rate data (can be None from Garmin API)
                if "resting_heart_rate" in row and row["resting_heart_rate"]:
                    defaults["resting_heart_rate"] = int(float(row["resting_heart_rate"]))
                if "min_heart_rate" in row and row["min_heart_rate"]:
                    defaults["min_heart_rate"] = int(float(row["min_heart_rate"]))
                if "max_heart_rate" in row and row["max_heart_rate"]:
                    defaults["max_heart_rate"] = int(float(row["max_heart_rate"]))

                DailyLog.objects.update_or_create(
                    date=date_str,
                    defaults=defaults,
                )
                count += 1

        self.stdout.write(self.style.SUCCESS(f"✅ Daily stats: Imported/updated {count} rows from {csv_path}"))

        # Import activities
        activities_path = Path(options["activities_csv"]).resolve()
        
        if not activities_path.exists():
            self.stdout.write(self.style.WARNING(f"⚠️ Activities CSV not found: {activities_path} (skipping)"))
            return
        
        with activities_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        
        imported = 0
        updated = 0
        
        for row in rows:
            activity_id = int(row['activity_id'])
            
            # Parse start_time
            start_time = datetime.fromisoformat(row['start_time'].replace(' ', 'T'))
            
            # Prepare data
            data = {
                'activity_type': row['activity_type'],
                'date': row['date'],
                'start_time': start_time,
                'name': row['name'],
                'duration_minutes': float(row['duration_minutes']),
                'distance_km': float(row['distance_km']) if row['distance_km'] else None,
                'pace_min_per_km': float(row['pace_min_per_km']) if row['pace_min_per_km'] else None,
                'avg_speed_kmh': float(row['avg_speed_kmh']) if row['avg_speed_kmh'] else None,
                'avg_hr': int(float(row['avg_hr'])) if row['avg_hr'] else None,
                'max_hr': int(float(row['max_hr'])) if row['max_hr'] else None,
                'calories': int(float(row['calories'])) if row['calories'] else None,
                'elevation_gain': float(row['elevation_gain']) if row['elevation_gain'] else None,
                'elevation_loss': float(row['elevation_loss']) if row['elevation_loss'] else None,
                'avg_cadence': float(row['avg_cadence']) if row['avg_cadence'] else None,
                'steps': int(float(row['steps'])) if row['steps'] else None,
                'vo2max': float(row['vo2max']) if row['vo2max'] else None,
                'hr_zone1_seconds': int(float(row['hr_zone1_seconds'])) if row.get('hr_zone1_seconds') else None,
                'hr_zone2_seconds': int(float(row['hr_zone2_seconds'])) if row.get('hr_zone2_seconds') else None,
                'hr_zone3_seconds': int(float(row['hr_zone3_seconds'])) if row.get('hr_zone3_seconds') else None,
                'hr_zone4_seconds': int(float(row['hr_zone4_seconds'])) if row.get('hr_zone4_seconds') else None,
                'hr_zone5_seconds': int(float(row['hr_zone5_seconds'])) if row.get('hr_zone5_seconds') else None,
            }
            
            # Create or update
            activity, created = Activity.objects.update_or_create(
                activity_id=activity_id,
                defaults=data
            )
            
            if created:
                imported += 1
            else:
                updated += 1
        
        self.stdout.write(
            self.style.SUCCESS(
                f'✅ Activities: Imported {imported} new, updated {updated} existing (total {len(rows)} activities)'
            )
        )        
        # Import activity laps
        laps_path = Path(options["laps_csv"]).resolve()
        
        if not laps_path.exists():
            self.stdout.write(self.style.WARNING(f"⚠️ Activity laps CSV not found: {laps_path} (skipping)"))
            return
        
        with laps_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            lap_rows = list(reader)
        
        laps_imported = 0
        laps_updated = 0
        last_lap_numbers = {}
        
        for row in lap_rows:
            activity_id = int(row['activity_id'])
            lap_number = int(row['lap_number'])
            
            # Find the parent activity
            try:
                activity = Activity.objects.get(activity_id=activity_id)
            except Activity.DoesNotExist:
                self.stdout.write(
                    self.style.WARNING(
                        f"⚠️ Activity {activity_id} not found for lap {lap_number}, skipping lap"
                    )
                )
                continue
            
            # Prepare lap data
            lap_data = {
                'step_type': row['step_type'],
                'lap_time_seconds': int(row['lap_time_seconds']),
                'cumulative_time_seconds': int(row['cumulative_time_seconds']),
                'distance_km': float(row['distance_km']) if row['distance_km'] else None,
                'avg_pace_min_per_km': float(row['avg_pace_min_per_km']) if row['avg_pace_min_per_km'] else None,
                'avg_speed_kmh': float(row['avg_speed_kmh']) if row['avg_speed_kmh'] else None,
                'avg_hr': int(float(row['avg_hr'])) if row['avg_hr'] else None,
                'max_hr': int(float(row['max_hr'])) if row['max_hr'] else None,
                'total_ascent_m': float(row['total_ascent_m']) if row['total_ascent_m'] else None,
                'total_descent_m': float(row['total_descent_m']) if row['total_descent_m'] else None,
                'avg_cadence': float(row['avg_cadence']) if row['avg_cadence'] else None,
                # Older CSVs have no workout_step column. in_analysis is the
                # user's own choice and is never set by an import.
                'workout_step': int(row['workout_step']) if row.get('workout_step') else None,
            }
            
            # Create or update lap
            lap, created = ActivityLap.objects.update_or_create(
                activity=activity,
                lap_number=lap_number,
                defaults=lap_data
            )
            
            if created:
                laps_imported += 1
            else:
                laps_updated += 1
            last_lap_numbers[activity.pk] = max(last_lap_numbers.get(activity.pk, 0), lap_number)

        # A re-fetched activity can have fewer laps than before (e.g. Garmin's
        # lap times instead of the old moving-time split) - drop the leftovers.
        laps_removed = 0
        for activity_pk, last_number in last_lap_numbers.items():
            laps_removed += ActivityLap.objects.filter(activity_id=activity_pk, lap_number__gt=last_number).delete()[0]
        if laps_removed:
            self.stdout.write(f'Laps: removed {laps_removed} leftover laps beyond the re-imported ones')
        
        self.stdout.write(
            self.style.SUCCESS(
                f'✅ Laps: Imported {laps_imported} new, updated {laps_updated} existing (total {len(lap_rows)} laps)'
            )
        )
