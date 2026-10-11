"""
Read-only Health Analytics v1: 7/28/90-day trends over the existing
DailyHealth / SleepSummary / HrvSummary / DailyLog models.

No writes, no new models, no medical/readiness/recovery interpretation -
just null-aware averages, sample counts, and "latest known value" per metric.
"""

from datetime import timedelta

from django.db.models import Avg, Count

from core.models import DailyLog
from health.models import DailyHealth, HrvSummary, SleepSummary

_WINDOWS = (7, 28, 90)


def get_period_start(end_date, days):
    """Inclusive window start: `days` calendar dates ending at (and including) end_date."""
    return end_date - timedelta(days=days - 1)


def _avg_and_count(queryset, date_field, value_field, end_date, days):
    """Null-aware average + sample count for one inclusive window.

    Avg()/Count(field) both ignore NULLs at the SQL level, so missing
    measurements are never treated as zero and never inflate the sample count.
    """
    start_date = get_period_start(end_date, days)
    agg = queryset.filter(**{
        f"{date_field}__gte": start_date,
        f"{date_field}__lte": end_date,
    }).aggregate(avg=Avg(value_field), count=Count(value_field))
    return agg["avg"], agg["count"]


def _latest_non_null(queryset, date_field, value_field, end_date):
    """Latest (value, date) for value_field on/before end_date, skipping NULLs."""
    obj = (
        queryset.filter(**{f"{date_field}__lte": end_date})
        .exclude(**{f"{value_field}__isnull": True})
        .order_by(f"-{date_field}")
        .first()
    )
    if obj is None:
        return None, None
    return getattr(obj, value_field), getattr(obj, date_field)


def _metric_trend(model, date_field, value_field, end_date, unit=None, metric_name=None):
    queryset = model.objects.all()
    current, current_date = _latest_non_null(queryset, date_field, value_field, end_date)

    windows = {}
    for days in _WINDOWS:
        windows[days] = _avg_and_count(queryset, date_field, value_field, end_date, days)

    avg_28d = windows[28][0]
    delta_vs_28d = (current - avg_28d) if (current is not None and avg_28d is not None) else None

    result = {
        "metric": metric_name,
        "current": current,
        "current_date": current_date,
        "avg_7d": windows[7][0],
        "avg_28d": avg_28d,
        "avg_90d": windows[90][0],
        "delta_vs_28d": delta_vs_28d,
        "sample_count_7d": windows[7][1],
        "sample_count_28d": windows[28][1],
        "sample_count_90d": windows[90][1],
    }
    if unit is not None:
        result["unit"] = unit
    return result


def get_hrv_analytics(end_date):
    trend = _metric_trend(HrvSummary, "night_date", "last_night_avg", end_date, unit="ms", metric_name="hrv_last_night_avg")

    # Latest available HrvSummary record on/before end_date, independent of
    # whether that record's last_night_avg is itself null.
    latest_row = HrvSummary.objects.filter(night_date__lte=end_date).order_by("-night_date").first()
    trend["baseline_low"] = latest_row.baseline_low if latest_row else None
    trend["baseline_upper"] = latest_row.baseline_upper if latest_row else None
    trend["status"] = latest_row.status if latest_row else None
    return trend


def get_resting_hr_analytics(end_date):
    return _metric_trend(DailyLog, "date", "resting_heart_rate", end_date, unit="bpm", metric_name="resting_heart_rate")


def get_stress_analytics(end_date):
    trend = _metric_trend(DailyHealth, "date", "stress_avg", end_date, metric_name="stress_avg")
    stress_max_current, stress_max_current_date = _latest_non_null(
        DailyHealth.objects.all(), "date", "stress_max", end_date
    )
    trend["stress_max_current"] = stress_max_current
    trend["stress_max_current_date"] = stress_max_current_date
    return trend


def get_body_battery_analytics(end_date):
    return {
        "start": _metric_trend(DailyHealth, "date", "body_battery_start", end_date, metric_name="body_battery_start"),
        "end": _metric_trend(DailyHealth, "date", "body_battery_end", end_date, metric_name="body_battery_end"),
        "charged": _metric_trend(DailyHealth, "date", "body_battery_charged", end_date, metric_name="body_battery_charged"),
        "drained": _metric_trend(DailyHealth, "date", "body_battery_drained", end_date, metric_name="body_battery_drained"),
    }


def get_sleep_analytics(end_date):
    duration = _metric_trend(SleepSummary, "wake_date", "total_sleep_seconds", end_date, unit="seconds", metric_name="total_sleep_seconds")
    for key in ("current", "avg_7d", "avg_28d", "avg_90d"):
        duration[f"{key}_hours"] = (duration[key] / 3600) if duration[key] is not None else None

    return {
        "duration": duration,
        "sleep_score": _metric_trend(SleepSummary, "wake_date", "sleep_score", end_date, metric_name="sleep_score"),
        "avg_heart_rate": _metric_trend(SleepSummary, "wake_date", "avg_heart_rate", end_date, unit="bpm", metric_name="sleep_avg_heart_rate"),
        "avg_stress": _metric_trend(SleepSummary, "wake_date", "avg_stress", end_date, metric_name="sleep_avg_stress"),
    }


def get_respiration_analytics(end_date):
    return {
        "waking": _metric_trend(DailyHealth, "date", "respiration_avg_waking", end_date, unit="breaths/min", metric_name="respiration_avg_waking"),
        "sleep": _metric_trend(SleepSummary, "wake_date", "avg_respiration", end_date, unit="breaths/min", metric_name="sleep_avg_respiration"),
    }


def get_spo2_analytics(end_date):
    return {
        "daily": _metric_trend(DailyHealth, "date", "spo2_avg", end_date, unit="%", metric_name="spo2_avg"),
        "sleep": _metric_trend(SleepSummary, "wake_date", "avg_spo2", end_date, unit="%", metric_name="sleep_avg_spo2"),
    }


def get_activity_analytics(end_date):
    return {
        "steps": _metric_trend(DailyLog, "date", "steps", end_date, metric_name="steps"),
        "moderate_intensity_minutes": _metric_trend(DailyLog, "date", "moderate_intensity_minutes", end_date, unit="minutes", metric_name="moderate_intensity_minutes"),
        "vigorous_intensity_minutes": _metric_trend(DailyLog, "date", "vigorous_intensity_minutes", end_date, unit="minutes", metric_name="vigorous_intensity_minutes"),
    }


def _daily_series(model, date_field, value_field, start_date, end_date):
    """One entry per calendar date in the inclusive [start_date, end_date] range.

    A date with no row (or a row whose value_field is null) gets value=None -
    never 0, never interpolated/forward-filled from a neighboring date.
    """
    rows = model.objects.filter(**{
        f"{date_field}__gte": start_date,
        f"{date_field}__lte": end_date,
    }).values_list(date_field, value_field)
    values_by_date = dict(rows)

    series = []
    current = start_date
    while current <= end_date:
        series.append({"date": current, "value": values_by_date.get(current)})
        current += timedelta(days=1)
    return series


def get_hrv_daily_series(start_date, end_date):
    return _daily_series(HrvSummary, "night_date", "last_night_avg", start_date, end_date)


def get_resting_hr_daily_series(start_date, end_date):
    return _daily_series(DailyLog, "date", "resting_heart_rate", start_date, end_date)


def get_health_analytics(end_date, start_date=None):
    """Top-level read-only entry point: 7/28/90-day trends across all Health
    metrics for end_date (unchanged, always relative to end_date).

    When start_date is given, hrv/resting_hr additionally get a
    "daily_series" list spanning the explicit [start_date, end_date] range -
    this does not change any existing aggregate calculation.
    """
    result = {
        "hrv": get_hrv_analytics(end_date),
        "resting_hr": get_resting_hr_analytics(end_date),
        "stress": get_stress_analytics(end_date),
        "body_battery": get_body_battery_analytics(end_date),
        "sleep": get_sleep_analytics(end_date),
        "respiration": get_respiration_analytics(end_date),
        "spo2": get_spo2_analytics(end_date),
        "activity": get_activity_analytics(end_date),
    }
    if start_date is not None:
        result["hrv"]["daily_series"] = get_hrv_daily_series(start_date, end_date)
        result["resting_hr"]["daily_series"] = get_resting_hr_daily_series(start_date, end_date)
    return result
