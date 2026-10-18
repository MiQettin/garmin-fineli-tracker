from django.db import models

def _format_pace_min_per_km(pace_min_per_km: float) -> str | None:
    """Format pace (min/km) as M:SS, rounded to whole seconds like Garmin Connect."""
    if not pace_min_per_km or pace_min_per_km <= 0:
        return None
    total_seconds = int(round(pace_min_per_km * 60))
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


class Activity(models.Model):
    """
    Model for tracking specific physical activities from Garmin.
    
    Stores detailed metrics for individual workout sessions including running,
    walking, dancing, strength training, and other activities.
    """
    
    ACTIVITY_TYPES = [
        ('running', 'Running'),
        ('walking', 'Walking'),
        ('cycling', 'Cycling'),
        ('strength', 'Strength Training'),
        ('dancing', 'Dancing'),
        ('other', 'Other'),
    ]
    
    activity_id = models.BigIntegerField(unique=True, help_text="Garmin activity ID")
    activity_type = models.CharField(max_length=20, choices=ACTIVITY_TYPES, default='other')
    date = models.DateField(db_index=True)
    start_time = models.DateTimeField()
    name = models.CharField(max_length=200, blank=True)
    
    # Duration and distance
    duration_minutes = models.FloatField(help_text="Activity duration in minutes")
    distance_km = models.FloatField(null=True, blank=True, help_text="Distance in kilometers")
    
    # Pace and speed (for running/walking)
    pace_min_per_km = models.FloatField(null=True, blank=True, help_text="Average pace (min/km)")
    avg_speed_kmh = models.FloatField(null=True, blank=True, help_text="Average speed (km/h)")
    
    # Heart rate
    avg_hr = models.IntegerField(null=True, blank=True, help_text="Average heart rate (bpm)")
    max_hr = models.IntegerField(null=True, blank=True, help_text="Maximum heart rate (bpm)")
    
    # Energy and effort
    calories = models.IntegerField(null=True, blank=True, help_text="Calories burned")
    
    # Elevation
    elevation_gain = models.FloatField(null=True, blank=True, help_text="Elevation gain (meters)")
    elevation_loss = models.FloatField(null=True, blank=True, help_text="Elevation loss (meters)")
    
    # Running-specific
    avg_cadence = models.FloatField(null=True, blank=True, help_text="Average cadence (steps/min)")
    steps = models.IntegerField(null=True, blank=True, help_text="Total steps")
    vo2max = models.FloatField(null=True, blank=True, help_text="VO2 max estimate")
    
    # HR zone time (seconds spent in each zone)
    hr_zone1_seconds = models.IntegerField(null=True, blank=True, help_text="Time in HR Zone 1 (seconds)")
    hr_zone2_seconds = models.IntegerField(null=True, blank=True, help_text="Time in HR Zone 2 (seconds)")
    hr_zone3_seconds = models.IntegerField(null=True, blank=True, help_text="Time in HR Zone 3 (seconds)")
    hr_zone4_seconds = models.IntegerField(null=True, blank=True, help_text="Time in HR Zone 4 (seconds)")
    hr_zone5_seconds = models.IntegerField(null=True, blank=True, help_text="Time in HR Zone 5 (seconds)")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-date', '-start_time']
        indexes = [
            models.Index(fields=['-date']),
            models.Index(fields=['activity_type', '-date']),
        ]
        verbose_name = "Activity"
        verbose_name_plural = "Activities"
        db_table = 'tracker_activity'  # Use existing table
    
    def __str__(self):
        return f"{self.get_activity_type_display()} - {self.date} ({self.duration_minutes:.1f} min)"


class ActivityLap(models.Model):
    """
    Model for tracking individual laps/intervals within an activity.
    
    Stores detailed metrics for each interval or lap of a workout.
    """
    
    DEFAULT_ANALYSIS_STEP_TYPES = ("run", "interval")

    STEP_TYPES = [
        ('warmup', 'Warm Up'),
        ('run', 'Run'),
        ('cooldown', 'Cool Down'),
        ('interval', 'Interval'),
        ('recovery', 'Recovery'),
        ('rest', 'Rest'),
        ('other', 'Other'),
    ]
    
    activity = models.ForeignKey(
        Activity, 
        on_delete=models.CASCADE, 
        related_name='laps',
        help_text="Parent activity this lap belongs to"
    )
    lap_number = models.IntegerField(help_text="Sequential lap number (1, 2, 3...)")
    step_type = models.CharField(
        max_length=20, 
        choices=STEP_TYPES, 
        default='run',
        help_text="Type of interval (warmup, run, cooldown, etc.)"
    )
    
    # Timing
    lap_time_seconds = models.IntegerField(help_text="Duration of this lap in seconds")
    cumulative_time_seconds = models.IntegerField(
        help_text="Total elapsed time up to end of this lap"
    )
    
    # Distance
    distance_km = models.FloatField(
        null=True, 
        blank=True, 
        help_text="Distance covered in this lap (km)"
    )
    
    # Pace and speed
    avg_pace_min_per_km = models.FloatField(
        null=True, 
        blank=True, 
        help_text="Average pace for this lap (min/km)"
    )
    avg_speed_kmh = models.FloatField(
        null=True, 
        blank=True, 
        help_text="Average speed for this lap (km/h)"
    )
    
    # Heart rate
    avg_hr = models.IntegerField(
        null=True, 
        blank=True, 
        help_text="Average heart rate during this lap (bpm)"
    )
    max_hr = models.IntegerField(
        null=True, 
        blank=True, 
        help_text="Maximum heart rate during this lap (bpm)"
    )
    
    # Elevation
    total_ascent_m = models.FloatField(
        null=True, 
        blank=True, 
        help_text="Elevation gained in this lap (meters)"
    )
    total_descent_m = models.FloatField(
        null=True, 
        blank=True, 
        help_text="Elevation lost in this lap (meters)"
    )
    
    # Running-specific
    avg_cadence = models.FloatField(
        null=True, 
        blank=True, 
        help_text="Average cadence for this lap (steps/min)"
    )

    # Workout structure and the user's own analysis choice
    workout_step = models.IntegerField(
        null=True,
        blank=True,
        help_text="Garmin workout step (wktStepIndex); auto-laps of one step share it",
    )
    in_analysis = models.BooleanField(
        null=True,
        blank=True,
        help_text="Counted in running analytics; empty = default (run and interval laps). Set per workout step by the user",
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['activity', 'lap_number']
        indexes = [
            models.Index(fields=['activity', 'lap_number']),
        ]
        unique_together = [['activity', 'lap_number']]
        verbose_name = "Activity Lap"
        verbose_name_plural = "Activity Laps"
        db_table = 'tracker_activitylap'  # Use existing table
    
    def __str__(self):
        return f"{self.activity.name} - Lap {self.lap_number} ({self.get_step_type_display()})"
    
    @property
    def counts_in_analysis(self):
        """The user's choice, or by default Garmin's run and interval laps
        (no warm-up, recovery, rest or cool-down)."""
        if self.in_analysis is None:
            return self.step_type in self.DEFAULT_ANALYSIS_STEP_TYPES
        return self.in_analysis

    def lap_time_formatted(self):
        """Format lap time as MM:SS or HH:MM:SS string."""
        hours = self.lap_time_seconds // 3600
        minutes = (self.lap_time_seconds % 3600) // 60
        seconds = self.lap_time_seconds % 60
        
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes}:{seconds:02d}"
    
    def pace_formatted(self):
        """Format pace as MM:SS string per km."""
        return _format_pace_min_per_km(self.avg_pace_min_per_km)
