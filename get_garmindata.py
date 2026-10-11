"""
Garmin Connect Data Fetcher

This script fetches health and activity data from Garmin Connect and exports
it to CSV files for import into the HealthManager Django application.

PERSONAL-USE NOTICE: built for the author's own Garmin account and workouts;
it will be generalized for public use later. It relies on the unofficial
`garminconnect` library (https://github.com/cyberjunky/python-garminconnect,
MIT), not an official Garmin API, so it may break when Garmin changes its
service. Use only with your own account, at your own risk. HealthManager is
not affiliated with or endorsed by Garmin. Garmin® and Garmin Connect™ are
trademarks of Garmin Ltd. or its subsidiaries.

Features:
    - Fetches daily health statistics (last 30 days)
    - Fetches running activities with detailed metrics
    - Exports to CSV files in backend/data/
    - Supports all activity types (running, walking, dancing, strength)

Environment Variables Required:
    GARMIN_EMAIL: Your Garmin Connect email address
    GARMIN_PASSWD: Your Garmin Connect password

Output Files:
    - backend/data/garmin_daily.csv: Daily health stats (calories, steps, HR, etc.)
    - backend/data/garmin_activities.csv: Individual running workouts with metrics

Usage:
    1. Set environment variables:
       set GARMIN_EMAIL=your_email@example.com
       set GARMIN_PASSWD=your_password
    
    2. Run the script:
       python get_garmindata.py
    
    3. Import to database:
       cd backend
       python manage.py import_garmin_csv

Daily Stats Columns:
    - date, calories_out, active_kcal, bmr_kcal
    - steps, distance_meters
    - moderate_intensity_minutes, vigorous_intensity_minutes
    - resting_heart_rate, min_heart_rate, max_heart_rate

Activity Columns:
    - activity_id, activity_type, date, start_time, name
    - duration_minutes, distance_km, pace_min_per_km, avg_speed_kmh
    - avg_hr, max_hr, calories
    - elevation_gain, elevation_loss
    - avg_cadence, steps, vo2max
"""

import os
import sys
import csv
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from garminconnect import Garmin

def parse_date(value: str) -> date:
    """Parse a YYYY-MM-DD or DD.MM.YYYY date string."""
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"Invalid date '{value}'. Use YYYY-MM-DD or DD.MM.YYYY."
    )


def build_parser() -> argparse.ArgumentParser:
    """Create the command line parser."""
    parser = argparse.ArgumentParser(
        description="Fetch Garmin Connect data and export CSV files."
    )
    parser.add_argument(
        "--start-date",
        type=parse_date,
        help="Start date for backfill (YYYY-MM-DD or DD.MM.YYYY). Defaults to 30 days ago.",
    )
    parser.add_argument(
        "--end-date",
        type=parse_date,
        help="End date for backfill (inclusive). Defaults to today.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Default lookback window when --start-date is not provided (default: 30).",
    )
    return parser


def resolve_date_range(args: argparse.Namespace) -> tuple[date, date]:
    """Resolve the requested date range."""
    today = date.today()
    end_date = args.end_date or (today - timedelta(days=1))
    start_date = args.start_date or (end_date - timedelta(days=args.days - 1))

    if start_date > end_date:
        raise ValueError("--start-date must be on or before --end-date")

    return start_date, end_date


def sync_health_data(client: Garmin, start_date: date, end_date: date) -> None:
    """Fetch Garmin Health data (sleep, HRV, stress, Body Battery,
    respiration, SpO2) for each date in [start_date, end_date] using the
    given already-authenticated client, and persist it through the tested
    parser/persistence orchestration (health.garmin_sync).

    Reuses the same authenticated `client` as activity acquisition - no
    second Garmin login. Per-call exceptions are not caught here; they
    propagate the same way client.get_stats() failures already do in the
    daily-stats loop, so a real Garmin/API error remains visible rather
    than being silently treated as "no health data".
    """
    backend_dir = str(Path(__file__).resolve().parent / "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    from health.garmin_sync import sync_health_responses

    for offset in range((end_date - start_date).days + 1):
        current_date = start_date + timedelta(days=offset)
        cdate = current_date.isoformat()
        sync_health_responses(
            requested_date=current_date,
            sleep_data=client.get_sleep_data(cdate),
            hrv_data=client.get_hrv_data(cdate),
            stress_data=client.get_stress_data(cdate),
            body_battery_data=client.get_body_battery(cdate, cdate),
            respiration_data=client.get_respiration_data(cdate),
            spo2_data=client.get_spo2_data(cdate),
        )


def main() -> int:
    """Fetch Garmin data and write CSV exports."""
    # --- Load environment variables from .env file ---
    env_path = Path(__file__).resolve().parent / '.env'
    load_dotenv(env_path)

    # --- Check environment variables ---
    email = os.getenv("GARMIN_EMAIL")
    passwd = os.getenv("GARMIN_PASSWD")
    token_dir = os.path.join(Path(__file__).resolve().parent, '.garmintokens')

    if not email or not passwd:
        raise RuntimeError("GARMIN_EMAIL or GARMIN_PASSWD environment variable is missing")

    args = build_parser().parse_args()

    try:
        start_date, end_date = resolve_date_range(args)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1

    # --- Login to Garmin Connect using tokens if available ---
    os.makedirs(token_dir, exist_ok=True)
    client = Garmin(email, passwd)

    try:
        print("Trying to login using stored tokens...")
        client.login(token_dir)
        print("Login with tokens successful!")
    except Exception as e:
        print(f"Stored tokens failed or not found: {e}")
        print("Performing full login with credentials...")
        try:
            client.login()
            # Save tokens for next time
            client.garth.dump(token_dir)
            print("Tokens saved successfully for future use.")
        except Exception as e2:
            if "429" in str(e2):
                print("\n" + "="*60)
                print("🚨 GARMIN CLOUDFLARE BLOCK (HTTP 429) 🚨")
                print("="*60)
                print("Garmin has temporarily blocked your IP address from making login requests.")
                print("This usually happens when too many logins are attempted without saving tokens.")
                print("\nWORKAROUNDS:")
                print("1. Wait ~24 hours for the Cloudflare block to expire on Garmin's end.")
                print("2. Connect your PC to a completely different network (like a mobile hotspot) to do the first login. Once tokens are saved, you can switch back.")
                print("="*60 + "\n")
                return 1
            else:
                print(f"Login failed: {e2}")
                return 1

    print(f"Fetching activities from {start_date} to {end_date}...")

    print("Syncing Garmin Health data (sleep, HRV, stress, Body Battery, respiration, SpO2)...")
    sync_health_data(client, start_date, end_date)
    print("[OK] Garmin Health data synced")

    daily_rows = []
    for offset in range((end_date - start_date).days + 1):
        current_date = start_date + timedelta(days=offset)
        stats = client.get_stats(current_date.isoformat())

        calories_out = stats.get("activeKilocalories", 0) + stats.get("bmrKilocalories", 0)

        daily_rows.append({
            "date": current_date.isoformat(),
            "calories_out": calories_out,
            "active_kcal": stats.get("activeKilocalories", 0),
            "bmr_kcal": stats.get("bmrKilocalories", 0),
            "steps": stats.get("totalSteps", 0),
            "distance_meters": stats.get("totalDistanceMeters", 0),
            "moderate_intensity_minutes": stats.get("moderateIntensityMinutes", 0),
            "vigorous_intensity_minutes": stats.get("vigorousIntensityMinutes", 0),
            "resting_heart_rate": stats.get("restingHeartRate"),
            "min_heart_rate": stats.get("minHeartRate"),
            "max_heart_rate": stats.get("maxHeartRate"),
        })

    activities = client.get_activities_by_date(start_date.isoformat(), end_date.isoformat())
    running_activities = [
        activity for activity in activities
        if 'running' in activity.get('activityType', {}).get('typeKey', '').lower()
    ]

    activity_rows = []
    lap_rows = []

    for activity in running_activities:
        activity_id = activity.get('activityId')

        distance_km = activity.get('distance', 0) / 1000
        # 'duration' is Garmin's elapsed/official time - the field a manual
        # correction in Garmin Connect (e.g. matching a race's official
        # timing) updates. 'movingDuration' is GPS-derived and unaffected by
        # that correction, so it must not take priority over it here.
        duration_seconds = activity.get('duration', activity.get('movingDuration', 0)) or 0
        duration_minutes = duration_seconds / 60
        pace_min_per_km = duration_minutes / distance_km if distance_km > 0 else 0
        avg_speed_kmh = distance_km / (duration_seconds / 3600) if duration_seconds > 0 else 0

        hr_zone_data = {1: None, 2: None, 3: None, 4: None, 5: None}
        try:
            hr_zones = client.get_activity_hr_in_timezones(activity_id)
            if hr_zones:
                for zone in hr_zones:
                    zone_num = zone.get('zoneNumber')
                    if zone_num and 1 <= zone_num <= 5:
                        hr_zone_data[zone_num] = zone.get('secsInZone')
        except Exception:
            pass

        activity_rows.append({
            'activity_id': activity_id,
            'activity_type': 'running',
            'date': activity.get('startTimeLocal', '')[:10],
            'start_time': activity.get('startTimeLocal', ''),
            'name': activity.get('activityName', ''),
            'duration_minutes': round(duration_minutes, 2),
            'distance_km': round(distance_km, 3),
            'pace_min_per_km': round(pace_min_per_km, 4) if pace_min_per_km else 0,
            'avg_speed_kmh': round(avg_speed_kmh, 3) if avg_speed_kmh else 0,
            'avg_hr': activity.get('averageHR'),
            'max_hr': activity.get('maxHR'),
            'calories': activity.get('calories'),
            'elevation_gain': round(activity.get('elevationGain', 0), 1),
            'elevation_loss': round(activity.get('elevationLoss', 0), 1),
            'avg_cadence': activity.get('averageRunningCadenceInStepsPerMinute'),
            'steps': activity.get('steps'),
            'vo2max': activity.get('vO2MaxValue'),
            'hr_zone1_seconds': hr_zone_data[1],
            'hr_zone2_seconds': hr_zone_data[2],
            'hr_zone3_seconds': hr_zone_data[3],
            'hr_zone4_seconds': hr_zone_data[4],
            'hr_zone5_seconds': hr_zone_data[5],
        })

        try:
            workout_laps = []
            try:
                workout = client.get_activity_details(activity_id)
                if workout and 'metadataDTO' in workout and 'workoutSteps' in workout['metadataDTO']:
                    for step in workout['metadataDTO']['workoutSteps']:
                        workout_laps.append({
                            'stepType': step.get('stepType', {}).get('stepTypeKey'),
                            'duration': step.get('targetDuration'),
                            'intensity': step.get('intensity', {}).get('intensityKey')
                        })
            except Exception:
                pass

            all_laps = []
            try:
                splits = client.get_activity_splits(activity_id)
                if splits and isinstance(splits, list):
                    all_laps = splits
                elif splits and 'lapDTOs' in splits:
                    all_laps = splits['lapDTOs']
            except Exception:
                split_data = client.get_activity_split_summaries(activity_id)
                if split_data and 'splitSummaries' in split_data:
                    all_laps = split_data['splitSummaries']

            if all_laps:
                summaries = sorted(all_laps, key=lambda x: x.get('startTimeGMT', '') or x.get('beginTimestamp', ''))
                cumulative_time = 0

                for lap_num, lap in enumerate(summaries, 1):
                    lap_distance_km = lap.get('distance', 0) / 1000
                    lap_duration_seconds = int(lap.get('movingDuration', lap.get('duration', 0)) or 0)
                    cumulative_time += lap_duration_seconds

                    step_type_from_workout = None
                    if workout_laps and lap_num <= len(workout_laps):
                        workout_step = workout_laps[lap_num - 1]
                        step_key = workout_step.get('stepType', '').lower() if workout_step.get('stepType') else ''
                        intensity_key = workout_step.get('intensity', '').lower() if workout_step.get('intensity') else ''

                        if 'warmup' in step_key:
                            step_type_from_workout = 'warmup'
                        elif 'cooldown' in step_key:
                            step_type_from_workout = 'cooldown'
                        elif 'recovery' in step_key or 'recovery' in intensity_key:
                            step_type_from_workout = 'recovery'
                        elif 'active' in intensity_key or 'repeat' in step_key:
                            step_type_from_workout = 'run'
                        elif 'rest' in step_key:
                            step_type_from_workout = 'rest'

                    lap_pace = None
                    if lap_distance_km > 0:
                        lap_pace = (lap_duration_seconds / 60) / lap_distance_km

                    lap_speed = lap.get('averageSpeed')
                    if lap_speed:
                        lap_speed = lap_speed * 3.6

                    if step_type_from_workout:
                        step_type = step_type_from_workout
                    else:
                        split_type = str(
                            lap.get('splitType', '') or
                            lap.get('intervalType', '') or
                            lap.get('lapType', '') or ''
                        ).lower()

                        intensity = str(
                            lap.get('intensity', '') or
                            lap.get('intervalIntensity', '') or ''
                        ).lower()

                        if 'warmup' in split_type or 'warmup' in intensity:
                            step_type = 'warmup'
                        elif 'cooldown' in split_type or 'cooldown' in intensity:
                            step_type = 'cooldown'
                        elif 'recovery' in split_type or 'recovery' in intensity or 'rest' in intensity:
                            step_type = 'recovery'
                        elif 'active' in split_type or 'run' in split_type or 'active' in intensity:
                            step_type = 'run'
                        elif 'rest' in split_type or 'stand' in split_type:
                            step_type = 'rest'
                        else:
                            # No explicit step type/intensity from Garmin for
                            # this lap (a plain, unstructured run) - default to
                            # 'run' rather than guessing warmup/interval/
                            # recovery from pace and lap position, which
                            # mislabels ordinary auto-laps (e.g. a fast short
                            # trailing partial-km lap as an "interval").
                            step_type = 'run'

                    lap_rows.append({
                        'activity_id': activity_id,
                        'lap_number': lap_num,
                        'step_type': step_type,
                        'lap_time_seconds': lap_duration_seconds,
                        'cumulative_time_seconds': cumulative_time,
                        'distance_km': round(lap_distance_km, 3),
                        'avg_pace_min_per_km': round(lap_pace, 4) if lap_pace else None,
                        'avg_speed_kmh': round(lap_speed, 3) if lap_speed else None,
                        'avg_hr': lap.get('averageHR'),
                        'max_hr': lap.get('maxHR'),
                        'total_ascent_m': round(lap.get('elevationGain', 0), 1),
                        'total_descent_m': round(lap.get('elevationLoss', 0), 1),
                        'avg_cadence': lap.get('averageRunCadence') or lap.get('avgStepFrequency'),
                    })
        except Exception as e:
            print(f"⚠️  Warning: Could not fetch laps for activity {activity_id}: {e}")

    output_dir = "backend/data"
    os.makedirs(output_dir, exist_ok=True)

    daily_file = os.path.join(output_dir, "garmin_daily.csv")
    with open(daily_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "date", "calories_out", "active_kcal", "bmr_kcal",
                "steps", "distance_meters", "moderate_intensity_minutes",
                "vigorous_intensity_minutes", "resting_heart_rate",
                "min_heart_rate", "max_heart_rate"
            ]
        )
        writer.writeheader()
        writer.writerows(sorted(daily_rows, key=lambda x: x["date"]))

    print(f"[OK] Daily statistics: {daily_file}")

    activities_file = os.path.join(output_dir, "garmin_activities.csv")
    with open(activities_file, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            'activity_id', 'activity_type', 'date', 'start_time', 'name',
            'duration_minutes', 'distance_km', 'pace_min_per_km', 'avg_speed_kmh',
            'avg_hr', 'max_hr', 'calories', 'elevation_gain', 'elevation_loss',
            'avg_cadence', 'steps', 'vo2max',
            'hr_zone1_seconds', 'hr_zone2_seconds', 'hr_zone3_seconds',
            'hr_zone4_seconds', 'hr_zone5_seconds'
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sorted(activity_rows, key=lambda x: x['date']))

    print(f"[OK] Running activities ({len(activity_rows)} activities): {activities_file}")

    laps_file = os.path.join(output_dir, "garmin_activity_laps.csv")
    with open(laps_file, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            'activity_id', 'lap_number', 'step_type', 'lap_time_seconds',
            'cumulative_time_seconds', 'distance_km', 'avg_pace_min_per_km',
            'avg_speed_kmh', 'avg_hr', 'max_hr', 'total_ascent_m',
            'total_descent_m', 'avg_cadence'
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(lap_rows)

    print(f"[OK] Activity laps ({len(lap_rows)} laps): {laps_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
