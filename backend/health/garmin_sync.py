"""
Garmin Health sync orchestration.

Coordinates the pure parser layer (garmin_parser.py) and the persistence
layer (garmin_persistence.py) against already-fetched raw Garmin responses.

Does not authenticate, fetch Garmin data, read files, read environment
variables, or know about the dashboard/CLI - callers own acquiring the raw
responses and pass them in as plain decoded Python values.
"""

from .garmin_parser import parse_daily_health, parse_hrv_summary, parse_sleep_summary, parse_weigh_ins
from .garmin_persistence import insert_weigh_ins, upsert_daily_health, upsert_hrv_summary, upsert_sleep_summary
from .weight import sync_settings_weight


def sync_health_responses(
    requested_date,
    sleep_data,
    hrv_data,
    stress_data,
    body_battery_data,
    respiration_data,
    spo2_data,
):
    """Parse and persist one day's already-fetched raw Garmin health responses.

    sleep_data may be None, meaning Garmin has no sleep record for that date
    at all (watch not worn, no sleep detected, etc.) - that is treated the
    same way as hrv_data=None: no SleepSummary write, sleep_summary=None,
    other records continue. A non-empty but malformed sleep_data (missing
    dailySleepDTO or its required fields) is NOT treated as absence: it is
    still passed to parse_sleep_summary(), whose structural errors are
    allowed to propagate here rather than being swallowed into a fake,
    all-null SleepSummary. Only an explicit None is a proven "no data"
    shape for this endpoint (see backend/health/garmin_parser.py's
    docstring); an empty dict is not treated as absence since that has not
    been observed as a real Garmin "no sleep" response.

    hrv_data may be None (Garmin HRV unavailable for that night); that
    already resolves to "no HrvSummary row" through the parser/persistence
    layers, unchanged here.

    stress_data/body_battery_data/respiration_data/spo2_data may be
    None/empty; parse_daily_health() already tolerates that per-source and
    leaves the corresponding DailyHealth fields as None.
    """
    daily_health_data = parse_daily_health(
        requested_date, stress_data, body_battery_data, respiration_data, spo2_data
    )
    daily_health = upsert_daily_health(daily_health_data)

    if sleep_data is None:
        sleep_summary = None
    else:
        sleep_summary_data = parse_sleep_summary(sleep_data)
        sleep_summary = upsert_sleep_summary(sleep_summary_data)

    hrv_summary_data = parse_hrv_summary(hrv_data)
    hrv_summary = upsert_hrv_summary(hrv_summary_data)

    return {
        "daily_health": daily_health,
        "sleep_summary": sleep_summary,
        "hrv_summary": hrv_summary,
    }


def sync_weigh_ins_response(weigh_ins_response):
    """Parse and store an already-fetched get_weigh_ins() response, then
    bring the weight in Settings up to date. Returns the number of new
    weigh-ins."""
    added = insert_weigh_ins(parse_weigh_ins(weigh_ins_response))
    sync_settings_weight()
    return added
