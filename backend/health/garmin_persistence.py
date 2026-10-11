"""
Idempotent persistence for normalized Garmin Health parser output.

Takes the dicts returned by health.garmin_parser.parse_daily_health() /
parse_sleep_summary() / parse_hrv_summary() and writes them to DailyHealth /
SleepSummary / HrvSummary via update_or_create.

Does not parse raw Garmin JSON and does not touch Garmin/network/auth in
any way - callers are responsible for fetching and normalizing data first.
"""

from .models import DailyHealth, HrvSummary, SleepSummary

_DAILY_HEALTH_FIELDS = {
    "stress_avg", "stress_max",
    "respiration_avg_waking", "respiration_min", "respiration_max",
    "spo2_avg", "spo2_min", "spo2_latest", "spo2_latest_at",
    "body_battery_start", "body_battery_end", "body_battery_min", "body_battery_max",
    "body_battery_charged", "body_battery_drained",
}

_SLEEP_SUMMARY_FIELDS = {
    "wake_date", "sleep_start", "sleep_end", "total_sleep_seconds",
    "deep_sleep_seconds", "light_sleep_seconds", "rem_sleep_seconds",
    "awake_seconds", "awake_count", "sleep_score", "sleep_score_qualifier",
    "avg_heart_rate", "avg_stress", "avg_respiration", "avg_spo2", "lowest_spo2",
    "sleep_need_baseline_minutes", "sleep_need_actual_minutes",
}

_HRV_SUMMARY_FIELDS = {
    "weekly_avg", "last_night_avg", "last_night_5min_high",
    "baseline_low", "baseline_upper", "status", "marker_value",
}


def _defaults(data, allowed_fields):
    """Pick only known model fields out of a normalized dict, so unexpected
    parser/Garmin keys can never leak into an ORM write."""
    return {key: data[key] for key in allowed_fields if key in data}


def upsert_daily_health(data):
    """Idempotently persist one parse_daily_health() dict, keyed by date."""
    instance, _ = DailyHealth.objects.update_or_create(
        date=data["date"],
        defaults=_defaults(data, _DAILY_HEALTH_FIELDS),
    )
    return instance


def upsert_sleep_summary(data):
    """Idempotently persist one parse_sleep_summary() dict, keyed by garmin_sleep_id.

    wake_date is stored data, not the lookup key - a corrected wake_date for
    the same Garmin sleep session updates the existing row.
    """
    instance, _ = SleepSummary.objects.update_or_create(
        garmin_sleep_id=data["garmin_sleep_id"],
        defaults=_defaults(data, _SLEEP_SUMMARY_FIELDS),
    )
    return instance


def upsert_hrv_summary(data):
    """Idempotently persist one parse_hrv_summary() dict, keyed by night_date.

    data may be None (Garmin HRV unavailable for that night) - in that case
    no database write happens and None is returned, mirroring
    parse_hrv_summary(None) -> None.
    """
    if data is None:
        return None

    instance, _ = HrvSummary.objects.update_or_create(
        night_date=data["night_date"],
        defaults=_defaults(data, _HRV_SUMMARY_FIELDS),
    )
    return instance
