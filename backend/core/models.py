from django.db import models


class UserSettings(models.Model):
    """
    Singleton model for user preferences and thresholds.
    
    Stores user physical data, calorie goals, macro targets, and display preferences.
    Only one instance should exist (pk=1), accessed via get_settings() classmethod.
    """
    
    # Calorie thresholds
    use_auto_thresholds = models.BooleanField(default=True, help_text="Derive warning thresholds from fitness goal")
    min_daily_calories = models.IntegerField(default=1500, help_text="Minimum recommended daily calorie intake")
    max_daily_calories = models.IntegerField(default=3000, help_text="Maximum recommended daily calorie intake")
    max_daily_deficit = models.IntegerField(default=300, help_text="Custom warning threshold for calorie deficit (kcal) - used when auto is off")
    garmin_calorie_adjustment = models.IntegerField(
        default=0,
        help_text="Calories to add to or subtract from Garmin daily expenditure",
    )
    
    # User physical data
    weight_kg = models.FloatField(null=True, blank=True, help_text="User weight in kilograms")
    height_cm = models.FloatField(null=True, blank=True, help_text="User height in centimeters")
    age = models.IntegerField(null=True, blank=True, help_text="User age in years")
    
    SEX_CHOICES = [
        ('M', 'Male'),
        ('F', 'Female'),
    ]
    sex = models.CharField(max_length=1, choices=SEX_CHOICES, null=True, blank=True, help_text="Biological sex for BMR calculation")
    
    ACTIVITY_CHOICES = [
        (1.2, 'Sedentary (little or no exercise)'),
        (1.375, 'Lightly active (1-3 days/week)'),
        (1.55, 'Moderately active (3-5 days/week)'),
        (1.725, 'Very active (6-7 days/week)'),
        (1.9, 'Extra active (athlete/physical job)'),
    ]
    activity_level = models.FloatField(choices=ACTIVITY_CHOICES, default=1.55, help_text="Activity level multiplier for TDEE")
    
    GOAL_CHOICES = [
        ('aggressive_cut', 'Aggressive Cut (-500 kcal) - Significant fat to lose'),
        ('moderate_cut', 'Moderate Cut (-300 kcal) - Standard fat loss'),
        ('mild_cut', 'Mild Cut (-200 kcal) - Lean/active individuals'),
        ('maintain', 'Maintenance (0 kcal) - Maintain current weight'),
        ('lean_bulk', 'Lean Bulk (+250 kcal) - Slow muscle gain'),
        ('bulk', 'Bulk (+400 kcal) - Faster muscle gain'),
    ]
    fitness_goal = models.CharField(max_length=20, choices=GOAL_CHOICES, default='maintain', help_text="Current fitness goal")
    
    # Macro goals (grams per day)
    protein_goal_g = models.FloatField(default=150.0, help_text="Daily protein goal in grams")
    carbs_goal_g = models.FloatField(default=200.0, help_text="Daily carbs goal in grams")
    fat_goal_g = models.FloatField(default=70.0, help_text="Daily fat goal in grams")
    
    # Week display settings
    WEEK_START_CHOICES = [
        (0, 'Monday'),
        (6, 'Sunday'),
    ]
    week_start_day = models.IntegerField(choices=WEEK_START_CHOICES, default=0, help_text="First day of the week")
    
    # Garmin sync tracking
    last_garmin_sync = models.DateTimeField(null=True, blank=True, help_text="Last successful Garmin data sync")
    
    # Metadata
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "User Settings"
        verbose_name_plural = "User Settings"
        db_table = 'tracker_usersettings'  # Use existing table
    
    def __str__(self):
        return "User Settings"
    
    @classmethod
    def get_settings(cls):
        """Get or create singleton settings instance"""
        settings, created = cls.objects.get_or_create(pk=1)
        return settings
    
    def calculate_bmr(self):
        """Calculate Basal Metabolic Rate using Mifflin-St Jeor equation"""
        if not all([self.weight_kg, self.height_cm, self.age, self.sex]):
            return None
        
        # Mifflin-St Jeor: BMR = (10 × weight in kg) + (6.25 × height in cm) - (5 × age in years) + s
        # s = +5 for males, -161 for females
        bmr = (10 * self.weight_kg) + (6.25 * self.height_cm) - (5 * self.age)
        if self.sex == 'M':
            bmr += 5
        else:
            bmr -= 161
        return bmr
    
    def calculate_tdee(self):
        """Calculate Total Daily Energy Expenditure"""
        bmr = self.calculate_bmr()
        if bmr is None:
            return None
        return bmr * self.activity_level
    
    def calculate_target_calories(self):
        """
        Calculate target calories based on TDEE and fitness goal.
        
        Calorie adjustments based on research:
        - Aggressive cut (-500): ~0.5kg/week loss, suitable for overweight/obese
        - Moderate cut (-300): ~0.3kg/week loss, standard recommendation for fat loss
        - Mild cut (-200): ~0.2kg/week loss, for lean individuals or very active people
        - Maintenance (0): Weight maintenance
        - Lean bulk (+250): ~0.25kg/month gain, minimize fat gain
        - Bulk (+400): ~0.4kg/month gain, faster muscle building
        """
        tdee = self.calculate_tdee()
        if tdee is None:
            return None
        
        goal_adjustments = {
            'aggressive_cut': -500,
            'moderate_cut': -300,
            'mild_cut': -200,
            'maintain': 0,
            'lean_bulk': 250,
            'bulk': 400,
        }
        
        adjustment = goal_adjustments.get(self.fitness_goal, 0)
        return tdee + adjustment
    
    def calculate_recommended_macros(self):
        """
        Calculate recommended macros based on target calories and goal.
        
        Macro distribution guidelines:
        - Protein: 1.8-2.2g per kg (higher for cutting to preserve muscle)
        - Fat: 20-30% of calories (lower during cutting, higher during bulking)
        - Carbs: Remaining calories (primary energy source)
        """
        target_cals = self.calculate_target_calories()
        if target_cals is None or self.weight_kg is None:
            return None
        
        # Protein: Higher during cutting to preserve muscle mass
        if self.fitness_goal in ['aggressive_cut', 'moderate_cut', 'mild_cut']:
            protein_g = self.weight_kg * 2.2  # Higher protein during deficit
            fat_pct = 0.25  # Lower fat % to allow more carbs for energy
        elif self.fitness_goal in ['lean_bulk', 'bulk']:
            protein_g = self.weight_kg * 1.8  # Slightly lower, still adequate
            fat_pct = 0.30  # Higher fat for hormone production
        else:  # maintain
            protein_g = self.weight_kg * 2.0
            fat_pct = 0.27
        
        fat_calories = target_cals * fat_pct
        fat_g = fat_calories / 9  # 9 kcal per gram of fat
        
        # Carbs: remaining calories
        protein_calories = protein_g * 4  # 4 kcal per gram
        remaining_calories = target_cals - protein_calories - fat_calories
        carbs_g = remaining_calories / 4  # 4 kcal per gram
        
        return {
            'protein_g': round(protein_g, 1),
            'carbs_g': round(max(0, carbs_g), 1),
            'fat_g': round(fat_g, 1),
            'target_calories': round(target_cals, 0),
        }


class DailyLog(models.Model):
    """
    Daily health statistics combining Garmin data and nutrition data.
    
    One row per day. Contains calorie balance, macros, activity metrics,
    and heart rate data from Garmin Connect.
    """
    date = models.DateField(unique=True)

    # Calorie data
    calories_out = models.IntegerField(default=0)
    calories_in = models.IntegerField(default=0)
    active_kcal = models.IntegerField(null=True, blank=True, help_text="Active calories component of calories_out (Garmin activeKilocalories)")
    bmr_kcal = models.IntegerField(null=True, blank=True, help_text="Resting/BMR calories component of calories_out (Garmin bmrKilocalories)")

    # Macro data
    fat_g = models.FloatField(default=0)
    sat_fat_g = models.FloatField(default=0)
    carbs_g = models.FloatField(default=0)
    protein_g = models.FloatField(default=0)
    sugar_g = models.FloatField(default=0)
    fiber_g = models.FloatField(default=0)
    salt_g = models.FloatField(default=0)

    # Activity data (from Garmin)
    steps = models.IntegerField(null=True, blank=True, help_text="Daily step count")
    distance_meters = models.IntegerField(null=True, blank=True, help_text="Daily distance in meters")
    moderate_intensity_minutes = models.IntegerField(null=True, blank=True, help_text="Moderate intensity activity minutes")
    vigorous_intensity_minutes = models.IntegerField(null=True, blank=True, help_text="Vigorous intensity activity minutes")
    
    # Heart rate data (from Garmin)
    resting_heart_rate = models.IntegerField(null=True, blank=True, help_text="Resting heart rate (bpm)")
    min_heart_rate = models.IntegerField(null=True, blank=True, help_text="Minimum heart rate (bpm)")
    max_heart_rate = models.IntegerField(null=True, blank=True, help_text="Maximum heart rate (bpm)")

    class Meta:
        ordering = ['-date']
        verbose_name = "Daily Log"
        verbose_name_plural = "Daily Logs"
        db_table = 'tracker_dailylog'  # Use existing table

    @property
    def balance_kcal(self) -> int:
        return self.calories_in - self.calories_out
    
    def recalculate_from_meals(self):
        """Recalculate calories_in and macros from MealEntry records.

        DailyLog is one shared row per date, so these stored totals include
        every user's entries. Never show them as one user's intake - use
        nutrition.aggregation.user_daily_totals() instead."""
        from django.db.models import Sum
        totals = self.meal_entries.aggregate(
            calories=Sum('calories'),
            protein=Sum('protein_g'),
            carbs=Sum('carbs_g'),
            fat=Sum('fat_g'),
            sat_fat=Sum('sat_fat_g'),
            sugar=Sum('sugar_g'),
            fiber=Sum('fiber_g'),
            salt=Sum('salt_g'),
        )
        self.calories_in = int(totals['calories'] or 0)
        self.protein_g = float(totals['protein'] or 0)
        self.carbs_g = float(totals['carbs'] or 0)
        self.fat_g = float(totals['fat'] or 0)
        self.sat_fat_g = float(totals['sat_fat'] or 0)
        self.sugar_g = float(totals['sugar'] or 0)
        self.fiber_g = float(totals['fiber'] or 0)
        self.salt_g = float(totals['salt'] or 0)
        self.save()

    def __str__(self) -> str:
        return str(self.date)
