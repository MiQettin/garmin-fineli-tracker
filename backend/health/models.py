from django.db import models


class DailyHealth(models.Model):
    """
    One row per local calendar day for Garmin health/recovery summaries
    that are not sleep-session-specific (stress, respiration, SpO2, Body Battery).
    """

    date = models.DateField(unique=True)

    # Stress (source: stress endpoint)
    stress_avg = models.IntegerField(null=True, blank=True, help_text="Daily average stress level")
    stress_max = models.IntegerField(null=True, blank=True, help_text="Daily maximum stress level")

    # Respiration (source: respiration endpoint)
    respiration_avg_waking = models.FloatField(null=True, blank=True, help_text="Average waking respiration (breaths/min)")
    respiration_min = models.FloatField(null=True, blank=True, help_text="Lowest respiration value for the day (breaths/min)")
    respiration_max = models.FloatField(null=True, blank=True, help_text="Highest respiration value for the day (breaths/min)")

    # SpO2 (source: SpO2 endpoint)
    spo2_avg = models.FloatField(null=True, blank=True, help_text="Daily average SpO2 (%)")
    spo2_min = models.FloatField(null=True, blank=True, help_text="Daily lowest SpO2 (%)")
    spo2_latest = models.FloatField(null=True, blank=True, help_text="Most recent SpO2 reading (%)")
    spo2_latest_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp of the latest SpO2 reading")

    # Body Battery (source: Body Battery endpoint; start/end/min/max derived later from the dense series)
    body_battery_start = models.IntegerField(null=True, blank=True, help_text="Body Battery value at start of day")
    body_battery_end = models.IntegerField(null=True, blank=True, help_text="Body Battery value at end of day")
    body_battery_min = models.IntegerField(null=True, blank=True, help_text="Lowest Body Battery value for the day")
    body_battery_max = models.IntegerField(null=True, blank=True, help_text="Highest Body Battery value for the day")
    body_battery_charged = models.IntegerField(null=True, blank=True, help_text="Body Battery gained during the day")
    body_battery_drained = models.IntegerField(null=True, blank=True, help_text="Body Battery spent during the day")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        verbose_name = "Daily Health"
        verbose_name_plural = "Daily Health"

    def __str__(self):
        return str(self.date)


class SleepSummary(models.Model):
    """One row per Garmin primary sleep session."""

    garmin_sleep_id = models.BigIntegerField(unique=True, help_text="Garmin sleep session ID (dailySleepDTO.id)")
    wake_date = models.DateField(db_index=True, help_text="Calendar date of wake-up (dailySleepDTO.calendarDate)")

    sleep_start = models.DateTimeField()
    sleep_end = models.DateTimeField()

    total_sleep_seconds = models.IntegerField()
    deep_sleep_seconds = models.IntegerField(null=True, blank=True)
    light_sleep_seconds = models.IntegerField(null=True, blank=True)
    rem_sleep_seconds = models.IntegerField(null=True, blank=True)
    awake_seconds = models.IntegerField(null=True, blank=True)
    awake_count = models.IntegerField(null=True, blank=True)

    sleep_score = models.IntegerField(null=True, blank=True, help_text="sleepScores.overall.value (0-100)")
    sleep_score_qualifier = models.CharField(max_length=20, null=True, blank=True, help_text="sleepScores.overall.qualifierKey, e.g. FAIR")

    avg_heart_rate = models.IntegerField(null=True, blank=True)
    avg_stress = models.FloatField(null=True, blank=True)

    # Authoritative source for sleep respiration/SpO2 - do not duplicate from the daily respiration/SpO2 endpoints.
    avg_respiration = models.FloatField(null=True, blank=True)
    avg_spo2 = models.FloatField(null=True, blank=True)
    lowest_spo2 = models.FloatField(null=True, blank=True)

    sleep_need_baseline_minutes = models.IntegerField(null=True, blank=True)
    sleep_need_actual_minutes = models.IntegerField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-wake_date']
        verbose_name = "Sleep Summary"
        verbose_name_plural = "Sleep Summaries"

    def __str__(self):
        return f"Sleep - {self.wake_date}"


class HrvSummary(models.Model):
    """
    Nightly Garmin HRV summary.

    Kept independent from SleepSummary (no FK/OneToOne): get_hrv_data() can
    return None even when the sleep endpoint succeeds, and vice versa.
    """

    night_date = models.DateField(unique=True, help_text="hrvSummary.calendarDate")

    weekly_avg = models.IntegerField(null=True, blank=True, help_text="Rolling weekly HRV average (ms)")
    last_night_avg = models.IntegerField(null=True, blank=True, help_text="Last-night HRV average (ms)")
    last_night_5min_high = models.IntegerField(null=True, blank=True, help_text="Highest 5-minute HRV window (ms)")

    baseline_low = models.IntegerField(null=True, blank=True, help_text="Personal balanced-range floor (ms)")
    baseline_upper = models.IntegerField(null=True, blank=True, help_text="Personal balanced-range ceiling (ms)")

    status = models.CharField(max_length=20, null=True, blank=True, help_text="e.g. BALANCED")
    marker_value = models.FloatField(null=True, blank=True, help_text="Position within the balanced range")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-night_date']
        verbose_name = "HRV Summary"
        verbose_name_plural = "HRV Summaries"

    def __str__(self):
        return f"HRV - {self.night_date}"
