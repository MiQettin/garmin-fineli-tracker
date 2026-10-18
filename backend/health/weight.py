"""Body weight: which measurement counts for a day, and keeping the weight
in Settings current.

Rules (used everywhere a weight is needed, e.g. g/kg nutrition targets):
- On a date with entries, a manual entry (or a Garmin entry corrected in
  the app) wins over Garmin entries - the most recently saved one; among
  Garmin entries the latest measurement counts.
- A date without entries uses the most recent earlier date that has one.
- Ignored entries (removed Garmin weigh-ins) never count.
- With no entries at all, callers fall back to UserSettings.weight_kg.
"""
from datetime import timedelta

from django.utils import timezone

from .models import WeightEntry


def _pick(entries):
    """The entry that counts among one date's entries."""
    manual = [e for e in entries if e.source == WeightEntry.SOURCE_MANUAL]
    if manual:  # the most recently saved manual value (or corrected Garmin entry)
        return max(manual, key=lambda e: (e.updated_at, e.id))
    return max(entries, key=lambda e: (e.measured_at or e.updated_at, e.id))


def entry_on(day):
    """The WeightEntry that counts for `day`, or None if there is none on or before it."""
    active = WeightEntry.objects.filter(ignored=False, date__lte=day)
    latest = active.order_by("-date").values_list("date", flat=True).first()
    if latest is None:
        return None
    return _pick(list(active.filter(date=latest)))


def weight_on(day):
    """Weight in kg for `day` (see the module rules), or None."""
    entry = entry_on(day)
    return entry.weight_kg if entry else None


def daily_weights(start, end):
    """{date: kg} for every date in [start, end], carrying the last known
    weight forward. Dates before the first entry are left out."""
    active = WeightEntry.objects.filter(ignored=False, date__lte=end)
    by_date = {}
    for entry in active.filter(date__gte=start):
        by_date.setdefault(entry.date, []).append(entry)
    current = weight_on(start - timedelta(days=1))
    result = {}
    day = start
    while day <= end:
        if day in by_date:
            current = _pick(by_date[day]).weight_kg
        if current is not None:
            result[day] = current
        day += timedelta(days=1)
    return result


def monthly_averages(start, end):
    """[(first day of month, average kg, measured days)] for the calendar
    months overlapping [start, end], oldest first. Each measured day counts
    once (the weight that counts for that day); months without measurements
    are left out. Used to watch that weight doesn't drop over the training
    season - an athlete's check, not a weight-loss metric."""
    by_date = {}
    for entry in WeightEntry.objects.filter(ignored=False, date__gte=start.replace(day=1), date__lte=end):
        by_date.setdefault(entry.date, []).append(entry)
    months = {}
    for day, entries in by_date.items():
        months.setdefault(day.replace(day=1), []).append(_pick(entries).weight_kg)
    return [(month, round(sum(kgs) / len(kgs), 1), len(kgs)) for month, kgs in sorted(months.items())]


def sync_settings_weight():
    """Copy the weight that counts today into UserSettings.weight_kg, so BMR,
    TDEE and macro recommendations use the latest weight. Does nothing when
    there are no entries."""
    from core.models import UserSettings

    weight = weight_on(timezone.localdate())
    if weight is None:
        return
    settings = UserSettings.get_settings()
    weight = round(weight, 1)
    if settings.weight_kg != weight:
        settings.weight_kg = weight
        settings.save(update_fields=["weight_kg", "updated_at"])


def record_manual_weight(day, weight_kg):
    """Create or update the one manual entry of `day` and sync Settings."""
    entry = (
        WeightEntry.objects.filter(date=day, source=WeightEntry.SOURCE_MANUAL, garmin_sample_id__isnull=True)
        .order_by("-id").first()
    )
    if entry is None:
        entry = WeightEntry(date=day, source=WeightEntry.SOURCE_MANUAL)
    entry.weight_kg = weight_kg
    entry.save()
    sync_settings_weight()
    return entry
