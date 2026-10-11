"""
Pure parser/normalizer for captured Garmin Health API responses.

Turns decoded Garmin JSON (as returned by garminconnect's get_sleep_data,
get_hrv_data, get_stress_data, get_body_battery, get_respiration_data,
get_spo2_data) into plain dicts matching the DailyHealth, SleepSummary, and
HrvSummary model fields.

No file IO, no network access, no Django ORM usage. Callers are responsible
for both fetching the raw Garmin JSON and persisting the normalized output.

Field-path/timestamp choices here are based on real captured responses
(backend/data/garmin_health_debug/2026-08-31_*.json), not assumptions:
- dailySleepDTO.sleepStartTimestampGMT / sleepEndTimestampGMT are epoch
  milliseconds (int).
- spo2 latestSpO2TimestampGMT is an ISO-8601-ish GMT string
  (e.g. "2026-08-31T21:00:00.0"), not epoch milliseconds.
These two are proven to use different representations under similarly named
fields, so they are handled by separate helpers rather than one generic
"Garmin timestamp" parser.
"""

from datetime import date, datetime, timezone

_BB_DESCRIPTOR_INDEX_KEY = "bodyBatteryValueDescriptorIndex"
_BB_DESCRIPTOR_NAME_KEY = "bodyBatteryValueDescriptorKey"


def _as_int(value):
    return None if value is None else int(value)


def _as_float(value):
    return None if value is None else float(value)


def _epoch_ms_to_utc_datetime(value):
    """Convert a Garmin epoch-millisecond timestamp to an aware UTC datetime."""
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def _iso_to_utc_datetime(value):
    """Convert a Garmin ISO-ish GMT string (e.g. '2026-08-31T21:00:00.0') to an aware UTC datetime."""
    if value is None:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def _iso_to_date(value):
    if value is None:
        return None
    return date.fromisoformat(value)


def parse_sleep_summary(sleep_response):
    """Normalize a get_sleep_data() response into SleepSummary field values.

    dailySleepDTO is the authoritative summary source. Required identity
    fields (id, calendarDate, start/end timestamps, total sleep time) are
    read with direct indexing so a structurally malformed response raises
    KeyError instead of silently producing an invalid record.
    """
    daily = sleep_response["dailySleepDTO"]

    sleep_scores = daily.get("sleepScores") or {}
    overall = sleep_scores.get("overall") or {}
    sleep_need = daily.get("sleepNeed") or {}

    return {
        "garmin_sleep_id": _as_int(daily["id"]),
        "wake_date": _iso_to_date(daily["calendarDate"]),
        "sleep_start": _epoch_ms_to_utc_datetime(daily["sleepStartTimestampGMT"]),
        "sleep_end": _epoch_ms_to_utc_datetime(daily["sleepEndTimestampGMT"]),
        "total_sleep_seconds": _as_int(daily["sleepTimeSeconds"]),
        "deep_sleep_seconds": _as_int(daily.get("deepSleepSeconds")),
        "light_sleep_seconds": _as_int(daily.get("lightSleepSeconds")),
        "rem_sleep_seconds": _as_int(daily.get("remSleepSeconds")),
        "awake_seconds": _as_int(daily.get("awakeSleepSeconds")),
        "awake_count": _as_int(daily.get("awakeCount")),
        "sleep_score": _as_int(overall.get("value")),
        "sleep_score_qualifier": overall.get("qualifierKey"),
        "avg_heart_rate": _as_int(daily.get("avgHeartRate")),
        "avg_stress": _as_float(daily.get("avgSleepStress")),
        "avg_respiration": _as_float(daily.get("averageRespirationValue")),
        "avg_spo2": _as_float(daily.get("averageSpO2Value")),
        "lowest_spo2": _as_float(daily.get("lowestSpO2Value")),
        "sleep_need_baseline_minutes": _as_int(sleep_need.get("baseline")),
        "sleep_need_actual_minutes": _as_int(sleep_need.get("actual")),
    }


def parse_hrv_summary(hrv_response):
    """Normalize a get_hrv_data() response into HrvSummary field values.

    get_hrv_data() can return None outright; that must propagate as None
    rather than becoming an all-null record.
    """
    if hrv_response is None:
        return None

    summary = hrv_response["hrvSummary"]
    baseline = summary.get("baseline") or {}

    return {
        "night_date": _iso_to_date(summary["calendarDate"]),
        "weekly_avg": _as_int(summary.get("weeklyAvg")),
        "last_night_avg": _as_int(summary.get("lastNightAvg")),
        "last_night_5min_high": _as_int(summary.get("lastNight5MinHigh")),
        "baseline_low": _as_int(baseline.get("balancedLow")),
        "baseline_upper": _as_int(baseline.get("balancedUpper")),
        "status": summary.get("status"),
        "marker_value": _as_float(baseline.get("markerValue")),
    }


def _match_body_battery_day(body_battery_response, target_date):
    """Find the (charged, drained) pair for target_date in a get_body_battery() list."""
    if not body_battery_response:
        return None, None
    target = target_date.isoformat()
    for entry in body_battery_response:
        if entry.get("date") == target:
            return entry.get("charged"), entry.get("drained")
    return None, None


def _resolve_body_battery_columns(descriptor_list):
    return {
        entry[_BB_DESCRIPTOR_NAME_KEY]: entry[_BB_DESCRIPTOR_INDEX_KEY]
        for entry in descriptor_list
    }


def _body_battery_range_from_stress(stress_response):
    """Derive (start, end, min, max) Body Battery level from the dense
    bodyBatteryValuesArray embedded in a get_stress_data() response.

    Column positions are resolved from bodyBatteryValueDescriptorsDTOList
    by name, never hardcoded, since Garmin does not guarantee column order.
    """
    if not stress_response:
        return None, None, None, None

    descriptors = stress_response.get("bodyBatteryValueDescriptorsDTOList")
    values = stress_response.get("bodyBatteryValuesArray")
    if not descriptors or not values:
        return None, None, None, None

    columns = _resolve_body_battery_columns(descriptors)
    if "timestamp" not in columns or "bodyBatteryLevel" not in columns:
        return None, None, None, None

    ts_idx = columns["timestamp"]
    level_idx = columns["bodyBatteryLevel"]

    samples = []
    for row in values:
        if len(row) <= max(ts_idx, level_idx):
            continue
        timestamp = row[ts_idx]
        level = row[level_idx]
        if timestamp is None or not isinstance(level, (int, float)):
            continue
        samples.append((timestamp, level))

    if not samples:
        return None, None, None, None

    samples.sort(key=lambda pair: pair[0])
    levels = [level for _, level in samples]

    return samples[0][1], samples[-1][1], min(levels), max(levels)


def parse_daily_health(target_date, stress_response, body_battery_response, respiration_response, spo2_response):
    """Normalize stress/body-battery/respiration/spo2 responses for one local
    calendar day into DailyHealth field values.

    Sleep-scoped respiration/SpO2 (avgSleepRespirationValue, avgSleepSpO2)
    are intentionally not read here - those belong to SleepSummary.
    "Tomorrow" sleep-prediction fields in the respiration/spo2 responses are
    also intentionally not read here - they describe a different date.
    """
    stress_response = stress_response or {}
    respiration_response = respiration_response or {}
    spo2_response = spo2_response or {}

    charged, drained = _match_body_battery_day(body_battery_response, target_date)
    bb_start, bb_end, bb_min, bb_max = _body_battery_range_from_stress(stress_response)

    return {
        "date": target_date,
        "stress_avg": _as_int(stress_response.get("avgStressLevel")),
        "stress_max": _as_int(stress_response.get("maxStressLevel")),
        "respiration_avg_waking": _as_float(respiration_response.get("avgWakingRespirationValue")),
        "respiration_min": _as_float(respiration_response.get("lowestRespirationValue")),
        "respiration_max": _as_float(respiration_response.get("highestRespirationValue")),
        "spo2_avg": _as_float(spo2_response.get("averageSpO2")),
        "spo2_min": _as_float(spo2_response.get("lowestSpO2")),
        "spo2_latest": _as_float(spo2_response.get("latestSpO2")),
        "spo2_latest_at": _iso_to_utc_datetime(spo2_response.get("latestSpO2TimestampGMT")),
        "body_battery_charged": _as_int(charged),
        "body_battery_drained": _as_int(drained),
        "body_battery_start": _as_int(bb_start),
        "body_battery_end": _as_int(bb_end),
        "body_battery_min": _as_int(bb_min),
        "body_battery_max": _as_int(bb_max),
    }
