"""Turn Garmin lap data (get_activity_splits()["lapDTOs"]) into lap rows.

Pure Python - no Django, no network - so get_garmindata.py and the tests
can both use it. Based on captured responses from a vívoactive 5
(backend/data/garmin_activity_debug/, not in Git):

- Each lap has `intensityType` (WARMUP, ACTIVE, RECOVERY, COOLDOWN, REST,
  INTERVAL) and, in a structured workout, `wktStepIndex` (the workout
  step the lap belongs to; auto-laps split one step into several laps).
- `duration` is the lap time Garmin Connect shows, and the laps' durations
  add up to the activity's duration. `movingDuration` leaves out stops,
  so paces from it are faster than Garmin's.

Watches without workout steps send ACTIVE laps without a step index; those
are plain running laps.
"""

# Garmin's own step type for each lap, as set in the workout - no
# interpretation of lap order or pace. ACTIVE is "run".
INTENSITY_TO_STEP = {
    "WARMUP": "warmup",
    "ACTIVE": "run",
    "RECOVERY": "recovery",
    "REST": "rest",
    "COOLDOWN": "cooldown",
    "INTERVAL": "interval",
}


def classify_laps(laps):
    """Step type for each lap, straight from Garmin's intensityType; laps
    without one (plain runs, other watches) are 'run'."""
    return [INTENSITY_TO_STEP.get(str(lap.get("intensityType") or "").upper(), "run") for lap in laps]


def lap_rows(activity_id, laps):
    """CSV/ORM rows for an activity's laps, in Garmin's lap order, with the
    lap time and pace as Garmin Connect shows them."""
    ordered = sorted(laps, key=lambda lap: (lap.get("lapIndex") or 0, lap.get("startTimeGMT") or ""))
    rows = []
    cumulative = 0
    for number, (lap, step_type) in enumerate(zip(ordered, classify_laps(ordered)), 1):
        seconds = int(round(lap.get("duration") or lap.get("elapsedDuration") or lap.get("movingDuration") or 0))
        km = (lap.get("distance") or 0) / 1000
        cumulative += seconds
        speed = lap.get("averageSpeed")
        rows.append({
            "activity_id": activity_id,
            "lap_number": number,
            "step_type": step_type,
            "lap_time_seconds": seconds,
            "cumulative_time_seconds": cumulative,
            "distance_km": round(km, 3),
            "avg_pace_min_per_km": round(seconds / 60 / km, 4) if km > 0 and seconds > 0 else None,
            "avg_speed_kmh": round(speed * 3.6, 3) if speed else None,
            "avg_hr": lap.get("averageHR"),
            "max_hr": lap.get("maxHR"),
            "total_ascent_m": round(lap.get("elevationGain") or 0, 1),
            "total_descent_m": round(lap.get("elevationLoss") or 0, 1),
            "avg_cadence": lap.get("averageRunCadence") or lap.get("avgStepFrequency"),
            "workout_step": lap.get("wktStepIndex"),
        })
    return rows
