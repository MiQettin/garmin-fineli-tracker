from datetime import timedelta
import subprocess
import sys
import os
from pathlib import Path

from django.shortcuts import render, redirect
from django.utils.timezone import now
from django.db.models import Sum
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.core.management import call_command

from .models import DailyLog, UserSettings
from .forms import UserSettingsForm
from activities.models import Activity
from nutrition.aggregation import logged_nutrition_days_count, user_daily_totals, weekly_macro_summary


def calculate_hr_baseline(recent_days, current_day):
    """
    Calculate 7-day weighted average resting HR baseline.
    More recent days have higher weight.
    Returns (baseline_hr, deviation_bpm, status, consecutive_elevated_days)
    """
    baseline_days = [d for d in recent_days if d.resting_heart_rate is not None]
    
    if len(baseline_days) < 3:
        return None, None, "insufficient_data", 0
    
    total_weighted_hr = 0
    total_weight = 0
    
    for idx, day in enumerate(baseline_days[-7:]):
        weight = idx + 1
        total_weighted_hr += day.resting_heart_rate * weight
        total_weight += weight
    
    baseline_hr = total_weighted_hr / total_weight if total_weight > 0 else None
    
    if baseline_hr is None or current_day.resting_heart_rate is None:
        return baseline_hr, None, "no_current_data", 0
    
    deviation_bpm = current_day.resting_heart_rate - baseline_hr
    
    if deviation_bpm <= 3:
        status = "normal"
    elif deviation_bpm <= 6:
        status = "light_load"
    elif deviation_bpm <= 10:
        status = "fatigue"
    else:
        status = "need_rest"
    
    consecutive_elevated = 0
    for day in reversed(baseline_days[-7:]):
        if day.resting_heart_rate and baseline_hr:
            day_deviation = day.resting_heart_rate - baseline_hr
            if day_deviation >= 7:
                consecutive_elevated += 1
            else:
                break
    
    return round(baseline_hr, 1), round(deviation_bpm, 1), status, consecutive_elevated


@login_required
def week_view(request):
    """Dashboard view showing weekly summary with decision-support focus."""
    user_settings = UserSettings.get_settings()
    week_offset = int(request.GET.get('week', 0))
    
    today = now().date()
    # The window ends yesterday, not today - today's data is still
    # incomplete/in-progress, so "last 7 days" means the last 7 finished
    # days rather than including a partial today.
    yesterday = today - timedelta(days=1)
    end_date = yesterday - timedelta(days=week_offset * 7)
    start_date = end_date - timedelta(days=6)

    qs = DailyLog.objects.filter(date__range=[start_date, end_date]).order_by("date")
    all_days = list(qs)

    # DailyLog is shared between users (one row per date, with the Garmin
    # data), so its stored nutrition totals would include every user's
    # entries. Intake and macros come from this user's own MealEntry rows.
    nutrition_by_date = user_daily_totals(request.user, start_date, end_date)
    no_nutrition = {key: 0 for key in ('calories_in', 'protein_g', 'carbs_g', 'fat_g', 'sat_fat_g')}

    def intake(day):
        return nutrition_by_date.get(day.date, no_nutrition)

    # Filter out days with BOTH calories_in = 0 AND calories_out = 0 (completely empty days)
    calorie_adjustment = user_settings.garmin_calorie_adjustment
    days = [d for d in all_days if intake(d)['calories_in'] > 0 or (d.calories_out or 0) > 0]
    adjusted_balance = {
        d.pk: intake(d)['calories_in'] - ((d.calories_out or 0) + calorie_adjustment)
        for d in days
    }
    num_days = len(days)

    # Daily Breakdown table always shows every calendar day in the 7-day
    # window, even ones with no DailyLog row at all (log=None) or no
    # logged data - unlike `days` above, which intentionally excludes
    # empty days from the averages so they don't skew avg_balance etc.
    # This is presentation-only: it never feeds any calculation.
    all_days_by_date = {d.date: d for d in all_days}
    full_week_days = []
    for i in range(7):
        date = start_date + timedelta(days=i)
        log = all_days_by_date.get(date)
        nutrition = nutrition_by_date.get(date)
        full_week_days.append({
            "date": date,
            "log": log,
            "nutrition": nutrition,
            "calories_out": (log.calories_out or 0) + calorie_adjustment if log else None,
            "balance_kcal": (
                (nutrition or no_nutrition)['calories_in']
                - ((log.calories_out or 0) + calorie_adjustment)
                if log else None
            ),
        })

    summary = {
        'calories_in': sum(intake(d)['calories_in'] for d in days),
        'calories_out': sum((d.calories_out or 0) + calorie_adjustment for d in days),
        'protein_g': sum(intake(d)['protein_g'] for d in days),
        'carbs_g': sum(intake(d)['carbs_g'] for d in days),
        'fat_g': sum(intake(d)['fat_g'] for d in days),
        'sat_fat_g': sum(intake(d)['sat_fat_g'] for d in days),
    }
    summary['balance_kcal'] = summary['calories_in'] - summary['calories_out']
    
    # === ENERGY CARD DATA ===
    energy = {}
    if num_days > 0:
        avg_balance = summary['balance_kcal'] / num_days
        energy['avg_balance'] = round(avg_balance, 0)
        energy['avg_intake'] = round(summary['calories_in'] / num_days, 0)
        energy['avg_expenditure'] = round(summary['calories_out'] / num_days, 0)
        
        # Intake percentage of expenditure
        if summary['calories_out'] > 0:
            energy['intake_pct'] = round((summary['calories_in'] / summary['calories_out']) * 100, 0)
        else:
            energy['intake_pct'] = 0
        
        # Get target deficit from fitness goal
        goal_deficits = {
            'aggressive_cut': -500,
            'moderate_cut': -300,
            'mild_cut': -200,
            'maintain': 0,
            'lean_bulk': 250,
            'bulk': 400,
        }
        target_balance = goal_deficits.get(user_settings.fitness_goal, -300)
        energy['target_balance'] = target_balance
        
        # Warning thresholds - auto or custom
        if user_settings.use_auto_thresholds:
            # Auto thresholds based on goal type
            warning_buffer = 100  # kcal below goal before warning
            danger_buffer = 200  # kcal below goal before danger
        else:
            # Custom thresholds from settings
            warning_buffer = user_settings.max_daily_deficit
            danger_buffer = user_settings.max_daily_deficit * 2
        
        # How far off from target? (negative means deeper deficit than planned)
        deviation_from_goal = avg_balance - target_balance
        energy['deviation'] = round(deviation_from_goal, 0)
        
        # Status based on deviation from personal goal
        if target_balance >= 0:  # Maintenance or bulk goals
            if avg_balance >= target_balance - warning_buffer:
                energy['status'] = 'good'
                energy['interpretation'] = 'On track for your goal'
                energy['color'] = 'success'
            elif avg_balance >= target_balance - danger_buffer:
                energy['status'] = 'moderate'
                energy['interpretation'] = f'{abs(int(deviation_from_goal))} kcal below target'
                energy['color'] = 'warning'
            else:
                energy['status'] = 'aggressive'
                energy['interpretation'] = f'Undereating - {abs(int(deviation_from_goal))} kcal below goal'
                energy['color'] = 'error'
        else:  # Cut/deficit goals
            if avg_balance > target_balance + warning_buffer:
                energy['status'] = 'surplus'
                energy['interpretation'] = 'Eating above target - slower progress'
                energy['color'] = 'info'
            elif avg_balance >= target_balance - warning_buffer:
                energy['status'] = 'good'
                energy['interpretation'] = 'On track for your goal'
                energy['color'] = 'success'
            elif avg_balance >= target_balance - danger_buffer:
                energy['status'] = 'moderate'
                energy['interpretation'] = f'{abs(int(deviation_from_goal))} kcal deeper - watch energy levels'
                energy['color'] = 'warning'
            else:
                energy['status'] = 'aggressive'
                energy['interpretation'] = f'{abs(int(deviation_from_goal))} kcal too deep - muscle loss risk'
                energy['color'] = 'error'
        
        # 3-day trend (compare last 3 days avg to previous 3 days avg)
        days_sorted = sorted(days, key=lambda x: x.date, reverse=True)
        if len(days_sorted) >= 6:
            last_3 = sum(adjusted_balance[d.pk] for d in days_sorted[:3]) / 3
            prev_3 = sum(adjusted_balance[d.pk] for d in days_sorted[3:6]) / 3
            if last_3 > prev_3 + 50:
                energy['trend'] = 'improving'
                energy['trend_text'] = 'Intake improving'
            elif last_3 < prev_3 - 50:
                energy['trend'] = 'worsening'
                energy['trend_text'] = 'Deficit deepening'
            else:
                energy['trend'] = 'stable'
                energy['trend_text'] = 'Stable pattern'
        elif len(days_sorted) >= 3:
            last_3 = sum(adjusted_balance[d.pk] for d in days_sorted[:3]) / 3
            energy['trend'] = 'stable'
            energy['trend_text'] = f'{round(last_3, 0):+.0f} kcal (3-day avg)'
        
        # Best and worst days
        days_with_balance = [d for d in days if adjusted_balance[d.pk] is not None]
        if days_with_balance:
            best_day = max(days_with_balance, key=lambda x: adjusted_balance[x.pk])
            worst_day = min(days_with_balance, key=lambda x: adjusted_balance[x.pk])
            energy['best_day'] = {'date': best_day.date, 'balance': adjusted_balance[best_day.pk]}
            energy['worst_day'] = {'date': worst_day.date, 'balance': adjusted_balance[worst_day.pk]}

        # Per-day balance vs the 7-day average, for the mobile Dashboard's
        # "tap the 7-day average" comparison panel - reuses days_sorted and
        # avg_balance (both already computed above), just reshaped for
        # display: no new query, no new domain calculation.
        energy['days_vs_avg'] = [
            {
                'date': d.date,
                'balance': adjusted_balance[d.pk],
                'delta': round(adjusted_balance[d.pk] - avg_balance, 0),
                'is_today': d.date == today,
            }
            for d in days_sorted
        ]
        energy['today_entry'] = next((d for d in energy['days_vs_avg'] if d['is_today'] and d['balance'] is not None), None)

    # === TRAINING CARD DATA ===
    training = {}
    
    # Get activities for this week
    week_activities = Activity.objects.filter(date__range=[start_date, end_date])
    
    if week_activities.exists():
        training['total_workouts'] = week_activities.count()
        training['total_time_min'] = round(sum(a.duration_minutes or 0 for a in week_activities), 0)
        training['total_distance_km'] = round(sum(a.distance_km or 0 for a in week_activities), 1)
        training['total_calories'] = sum(a.calories or 0 for a in week_activities)
        
        # Training contribution to deficit
        if summary['calories_out'] > 0:
            training['deficit_contribution_pct'] = round(
                (training['total_calories'] / (summary['calories_out'] - summary['calories_in'])) * 100, 0
            ) if summary['calories_out'] > summary['calories_in'] else 0
        
        # Training load assessment (based on hours per week)
        hours_per_week = training['total_time_min'] / 60
        if hours_per_week < 2:
            training['load_status'] = 'low'
            training['load_text'] = 'Light week'
        elif hours_per_week < 5:
            training['load_status'] = 'normal'
            training['load_text'] = 'Normal load'
        elif hours_per_week < 8:
            training['load_status'] = 'high'
            training['load_text'] = 'High volume'
        else:
            training['load_status'] = 'very_high'
            training['load_text'] = 'Very high - ensure recovery'
    
    # Daily activity data (steps, intensity)
    activity_days = qs.filter(steps__isnull=False)
    if activity_days.exists():
        training['avg_steps'] = round(activity_days.aggregate(Sum("steps"))["steps__sum"] / activity_days.count(), 0)
        training['total_moderate_min'] = qs.aggregate(Sum("moderate_intensity_minutes"))["moderate_intensity_minutes__sum"] or 0
        training['total_vigorous_min'] = qs.aggregate(Sum("vigorous_intensity_minutes"))["vigorous_intensity_minutes__sum"] or 0
    
    # === RECOVERY CARD DATA ===
    recovery = {}
    
    baseline_start = start_date - timedelta(days=14)
    baseline_days = list(DailyLog.objects.filter(
        date__range=[baseline_start, end_date]
    ).order_by("date"))
    
    recent_day_with_hr = qs.filter(resting_heart_rate__isnull=False).order_by("-date").first()
    if recent_day_with_hr:
        baseline_hr, deviation, hr_status, consecutive_elevated = calculate_hr_baseline(baseline_days, recent_day_with_hr)
        recovery['current_hr'] = recent_day_with_hr.resting_heart_rate
        recovery['baseline_hr'] = baseline_hr
        recovery['deviation'] = deviation
        recovery['consecutive_elevated'] = consecutive_elevated
        
        # Simplified status
        if hr_status == 'normal':
            recovery['status'] = 'ok'
            recovery['status_text'] = 'Ready to train'
            recovery['color'] = 'success'
        elif hr_status == 'light_load':
            recovery['status'] = 'monitor'
            recovery['status_text'] = 'Train cautiously'
            recovery['color'] = 'warning'
        else:
            recovery['status'] = 'reduce'
            recovery['status_text'] = 'Prioritize recovery'
            recovery['color'] = 'error'
    
    # === PROTEIN DATA (primary macro) ===
    protein = {}
    if num_days > 0:
        protein['avg'] = round(summary['protein_g'] / num_days, 0)
        protein['goal'] = user_settings.protein_goal_g
        protein['percent'] = round((protein['avg'] / protein['goal']) * 100, 0) if protein['goal'] > 0 else 0
        protein['status'] = 'good' if protein['percent'] >= 90 else 'low'
    
    # === SECONDARY MACROS (hidden by default) ===
    macros = {}
    if num_days > 0:
        macros['carbs_avg'] = round(summary['carbs_g'] / num_days, 0)
        macros['fat_avg'] = round(summary['fat_g'] / num_days, 0)
        macros['carbs_goal'] = user_settings.carbs_goal_g
        macros['fat_goal'] = user_settings.fat_goal_g
        macros['carbs_percent'] = round((macros['carbs_avg'] / macros['carbs_goal']) * 100, 0) if macros['carbs_goal'] > 0 else 0
        macros['fat_percent'] = round((macros['fat_avg'] / macros['fat_goal']) * 100, 0) if macros['fat_goal'] > 0 else 0
        macros['carbs_status'] = 'good' if 80 <= macros['carbs_percent'] <= 120 else ('high' if macros['carbs_percent'] > 120 else 'low')
        macros['fat_status'] = 'good' if 80 <= macros['fat_percent'] <= 120 else ('high' if macros['fat_percent'] > 120 else 'low')

    # === NUTRITION SUMMARY: energy-based macro distribution + truthful ===
    # === coverage, independent of the goal-percentage cards above.      ===
    # Reuses the existing DailyLog-derived energy/summary values as-is
    # (never recalculated here) and MealEntry-level truth only for the new
    # macro-energy distribution and its logged-days denominator - a day
    # with no MealEntry is unlogged, never treated as a 0-intake day.
    window_days = (end_date - start_date).days + 1
    logged_days = logged_nutrition_days_count(request.user, start_date, end_date)
    macro_cov = weekly_macro_summary(request.user, start_date, end_date)

    nutrition_summary = {
        'window_days': window_days,
        'logged_days': logged_days,
        'denominator_label': 'Average per day' if logged_days == window_days and logged_days > 0 else 'Average on logged days',
    }

    macro_energy = {}
    kcal_per_gram = {'protein_g': 4, 'carbs_g': 4, 'fat_g': 9}
    for key, label in [('protein_g', 'protein'), ('carbs_g', 'carbs'), ('fat_g', 'fat')]:
        cov = macro_cov[key]
        if logged_days > 0 and cov.coverage in ('partial', 'complete'):
            avg_g = round(cov.known_sum / logged_days, 1)
        else:
            avg_g = None  # unknown - never fabricated as 0
        macro_energy[label] = {
            'avg_g': avg_g,
            'coverage': cov.coverage,
            'kcal': cov.known_sum * kcal_per_gram[key],
        }

    all_complete = all(macro_energy[m]['coverage'] == 'complete' for m in ('protein', 'carbs', 'fat'))
    total_macro_kcal = sum(macro_energy[m]['kcal'] for m in ('protein', 'carbs', 'fat'))
    nutrition_summary['percentages_available'] = all_complete and total_macro_kcal > 0
    if nutrition_summary['percentages_available']:
        for m in ('protein', 'carbs', 'fat'):
            macro_energy[m]['percent'] = round(macro_energy[m]['kcal'] / total_macro_kcal * 100, 1)
    nutrition_summary['macros'] = macro_energy

    # Get recent activities (last 5) with deficit contribution
    recent_activities = list(Activity.objects.filter(
        date__range=[start_date, end_date]
    ).order_by('-date', '-start_time')[:5])
    
    prev_week = week_offset + 1
    next_week = week_offset - 1 if week_offset > 0 else None
    
    context = {
        "days": days,
        "full_week_days": full_week_days,
        "summary": summary,
        "energy": energy,
        "training": training,
        "recovery": recovery,
        "protein": protein,
        "macros": macros,
        "nutrition_summary": nutrition_summary,
        "start_date": start_date,
        "end_date": end_date,
        "prev_week": prev_week,
        "next_week": next_week,
        "week_offset": week_offset,
        "last_garmin_sync": user_settings.last_garmin_sync,
        "recent_activities": recent_activities,
    }
    
    return render(request, "core/week.html", context)


@login_required
def settings_view(request):
    """View for user settings/preferences."""
    user_settings = UserSettings.get_settings()
    message = None
    
    if request.method == "POST":
        form = UserSettingsForm(request.POST, instance=user_settings)
        if form.is_valid():
            form.save()
            message = "Settings saved successfully!"
            return redirect("settings")
    else:
        form = UserSettingsForm(instance=user_settings)
    
    # Calculate target calories for percentage display
    target_calories = None
    tdee = user_settings.calculate_tdee()
    if tdee:
        goal_adjustments = {
            'aggressive_cut': -500,
            'moderate_cut': -300,
            'mild_cut': -200,
            'maintain': 0,
            'lean_bulk': 250,
            'bulk': 400,
        }
        adjustment = goal_adjustments.get(user_settings.fitness_goal, 0)
        target_calories = tdee + adjustment
    
    return render(request, "core/settings.html", {
        "form": form,
        "message": message,
        "target_calories": target_calories,
    })


@require_POST
def sync_garmin(request):
    """
    Sync Garmin data: fetch from API and import to database.
    
    Process:
        1. Run get_garmindata.py script to fetch data from Garmin Connect
        2. Run import_garmin_csv management command to import the data
        3. Update last_garmin_sync timestamp
    
    Returns:
        JsonResponse with status and message
    """
    # Manual check (not @login_required): this endpoint is called via
    # fetch() and always expects a JSON body back (see core/templates/
    # core/week.html), so an anonymous caller must get a JSON 401 rather
    # than a redirect to the login page, which `response.json()` can't parse.
    if not request.user.is_authenticated:
        return JsonResponse({"status": "error", "message": "Authentication required"}, status=401)

    user_settings = UserSettings.get_settings()
    
    try:
        # Path to get_garmindata.py (in HealthManager root)
        project_root = Path(__file__).resolve().parents[2]
        script_path = project_root / "get_garmindata.py"
        
        if not script_path.exists():
            return JsonResponse({
                "status": "error",
                "message": f"Script not found: {script_path}"
            }, status=400)
        
        # Step 1: Run get_garmindata.py to fetch data from Garmin Connect
        # Pass current environment to ensure Garmin credentials are available
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(project_root),
            env=os.environ.copy(),
            timeout=300  # 5 minute timeout
        )
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            return JsonResponse({
                "status": "error",
                "message": f"Failed to fetch Garmin data: {error_msg[:500]}"
            }, status=500)
        
        # Step 2: Run import_garmin_csv management command
        from io import StringIO
        output = StringIO()
        call_command("import_garmin_csv", stdout=output)
        import_output = output.getvalue()
        
        # Step 3: Update last sync timestamp
        user_settings.last_garmin_sync = now()
        user_settings.save()
        
        return JsonResponse({
            "status": "success",
            "message": "Garmin data synced successfully!",
            "details": import_output[:500] if import_output else None,
            "synced_at": user_settings.last_garmin_sync.strftime("%d-%m-%Y %H:%M")
        })
        
    except subprocess.TimeoutExpired:
        return JsonResponse({
            "status": "error",
            "message": "Sync timed out. Try again later."
        }, status=504)
    except Exception as e:
        return JsonResponse({
            "status": "error",
            "message": f"Sync failed: {str(e)}"
        }, status=500)
