from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils.translation import gettext
from django.views.decorators.http import require_POST
from django.db.models import Sum, Avg, Min, Max, Count, Q
from django.db import models
import json
from urllib.parse import urlencode

from .lap_steps import analysis_totals, workout_steps
from .models import Activity, ActivityLap, _format_pace_min_per_km


@login_required
def activities_view(request):
    """Display Garmin activities filtered by activity type."""
    activity_type = request.GET.get('type', 'running')
    
    activities = Activity.objects.filter(activity_type=activity_type)
    
    stats = activities.aggregate(
        total_count=models.Count('id'),
        total_distance=Sum('distance_km'),
        total_duration=Sum('duration_minutes'),
        avg_pace=models.Avg('pace_min_per_km'),
        avg_hr=models.Avg('avg_hr'),
        total_calories=Sum('calories'),
    )
    
    if stats['total_count'] and stats['total_count'] > 0:
        stats['avg_distance'] = stats['total_distance'] / stats['total_count'] if stats['total_distance'] else 0
        stats['avg_duration'] = stats['total_duration'] / stats['total_count'] if stats['total_duration'] else 0
    else:
        stats['avg_distance'] = 0
        stats['avg_duration'] = 0
    
    context = {
        'activities': activities[:30],
        'activity_type': activity_type,
        'stats': stats,
    }
    
    return render(request, 'activities/activities.html', context)


@login_required
def activity_detail_view(request, activity_id):
    """Display detailed view of a single activity."""
    activity = get_object_or_404(Activity, pk=activity_id)
    laps = activity.laps.all().order_by('lap_number')
    
    # Group laps Garmin-style
    lap_groups = []
    interval_counter = 0
    
    if laps:
        i = 0
        laps_list = list(laps)
        
        while i < len(laps_list):
            current_lap = laps_list[i]
            
            if current_lap.step_type == 'warmup':
                warmup_laps = [current_lap]
                i += 1
                while i < len(laps_list) and laps_list[i].step_type == 'warmup':
                    warmup_laps.append(laps_list[i])
                    i += 1
                lap_groups.append({
                    'step_type': 'warmup',
                    'laps': warmup_laps,
                    'interval_number': None,
                    'is_grouped': True
                })
            
            elif current_lap.step_type == 'cooldown':
                cooldown_laps = [current_lap]
                i += 1
                while i < len(laps_list) and laps_list[i].step_type == 'cooldown':
                    cooldown_laps.append(laps_list[i])
                    i += 1
                lap_groups.append({
                    'step_type': 'cooldown',
                    'laps': cooldown_laps,
                    'interval_number': None,
                    'is_grouped': True
                })
            
            elif current_lap.step_type == 'run':
                interval_counter += 1
                lap_groups.append({
                    'step_type': 'run',
                    'laps': [current_lap],
                    'interval_number': interval_counter,
                    'is_grouped': False
                })
                i += 1
            
            elif current_lap.step_type == 'recovery':
                lap_groups.append({
                    'step_type': 'recovery',
                    'laps': [current_lap],
                    'interval_number': interval_counter,
                    'is_grouped': False
                })
                i += 1
            
            else:
                lap_groups.append({
                    'step_type': current_lap.step_type,
                    'laps': [current_lap],
                    'interval_number': None,
                    'is_grouped': False
                })
                i += 1
    
    lap_stats = {}
    if laps.exists():
        laps_with_pace = laps.filter(avg_pace_min_per_km__isnull=False)
        if laps_with_pace.exists():
            lap_stats['best_pace'] = laps_with_pace.order_by('avg_pace_min_per_km').first()
            lap_stats['worst_pace'] = laps_with_pace.order_by('-avg_pace_min_per_km').first()
        
        laps_with_hr = laps.filter(avg_hr__isnull=False)
        if laps_with_hr.exists():
            lap_stats['highest_hr'] = laps_with_hr.order_by('-avg_hr').first()
            lap_stats['lowest_hr'] = laps_with_hr.order_by('avg_hr').first()
    
    # Format duration
    total_seconds = int(activity.duration_minutes * 60)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    duration_formatted = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    # Format pace
    pace_formatted = None
    if activity.pace_min_per_km:
        pace_formatted = _format_pace_min_per_km(activity.pace_min_per_km)
    
    # Calculate HR zones
    hr_zones = None
    if activity.avg_hr and activity.max_hr:
        max_hr = activity.max_hr
        
        def format_zone_time(seconds):
            """Format seconds as mm:ss or hh:mm:ss."""
            if seconds is None:
                return None
            seconds = int(seconds)
            if seconds >= 3600:
                hours = seconds // 3600
                minutes = (seconds % 3600) // 60
                secs = seconds % 60
                return f"{hours}:{minutes:02d}:{secs:02d}"
            else:
                minutes = seconds // 60
                secs = seconds % 60
                return f"{minutes}:{secs:02d}"
        
        hr_zones = {
            'zone1': (int(max_hr * 0.5), int(max_hr * 0.6)),
            'zone2': (int(max_hr * 0.6), int(max_hr * 0.7)),
            'zone3': (int(max_hr * 0.7), int(max_hr * 0.8)),
            'zone4': (int(max_hr * 0.8), int(max_hr * 0.9)),
            'zone5': (int(max_hr * 0.9), max_hr),
            'zone1_time': format_zone_time(activity.hr_zone1_seconds),
            'zone2_time': format_zone_time(activity.hr_zone2_seconds),
            'zone3_time': format_zone_time(activity.hr_zone3_seconds),
            'zone4_time': format_zone_time(activity.hr_zone4_seconds),
            'zone5_time': format_zone_time(activity.hr_zone5_seconds),
        }
        
        avg_hr = activity.avg_hr
        if avg_hr < hr_zones['zone1'][1]:
            hr_zones['current_zone'] = 1
        elif avg_hr < hr_zones['zone2'][1]:
            hr_zones['current_zone'] = 2
        elif avg_hr < hr_zones['zone3'][1]:
            hr_zones['current_zone'] = 3
        elif avg_hr < hr_zones['zone4'][1]:
            hr_zones['current_zone'] = 4
        else:
            hr_zones['current_zone'] = 5
    
    steps = workout_steps(list(laps))
    for step in steps:
        step['pace_formatted'] = _format_pace_min_per_km(step['pace'])
        step['time_formatted'] = f"{step['seconds'] // 60}:{step['seconds'] % 60:02d}"

    context = {
        'activity': activity,
        'laps': laps,
        'steps': steps,
        'lap_groups': lap_groups,
        'lap_stats': lap_stats,
        'duration_formatted': duration_formatted,
        'pace_formatted': pace_formatted,
        'hr_zones': hr_zones,
    }
    
    return render(request, 'activities/activity_detail.html', context)

@login_required
@require_POST
def activity_analysis_steps_view(request, activity_id):
    """Save which workout steps running analytics counts. A step that
    matches the default is stored as "default" (in_analysis empty), so only
    real choices are kept."""
    activity = get_object_or_404(Activity, pk=activity_id)
    chosen = {int(pk) for pk in request.POST.getlist('lap') if pk.isdigit()}
    for lap in activity.laps.all():
        wanted = lap.pk in chosen
        default = lap.step_type in ActivityLap.DEFAULT_ANALYSIS_STEP_TYPES
        value = None if wanted == default else wanted
        if lap.in_analysis != value:
            lap.in_analysis = value
            lap.save(update_fields=['in_analysis', 'updated_at'])
    messages.success(request, gettext("Running analytics selection saved."))
    return redirect(reverse('activity_detail', args=[activity.pk]) + '#steps')


"""View for displaying running performance analytics with charts. Supports filtering by workout type (VK, PK, Pitkä) and search by activity name."""
@login_required
def running_analytics_view(request):
    """Display running performance analytics with charts."""
    search_query = request.GET.get('q', '').strip()
    workout_type = request.GET.get('type', '')
    # Toggles: also count warm-up / cool-down laps of every workout.
    include_warmup = request.GET.get('warmup', '0') != '0'
    include_cooldown = request.GET.get('cooldown', '0') != '0'
    extra_types = tuple(t for t, on in (('warmup', include_warmup), ('cooldown', include_cooldown)) if on)

    running_activities = Activity.objects.filter(activity_type='running')
    if workout_type == 'vk':
        running_activities = running_activities.filter(name__icontains='VK')
    elif workout_type == 'pk':
        running_activities = running_activities.filter(name__icontains='PK')
    elif workout_type == 'pitka':
        running_activities = running_activities.filter(
            Q(name__icontains='Pitkä') | Q(name__icontains='Pitka') | Q(name__icontains='Long')
        )
    elif search_query:
        running_activities = running_activities.filter(name__icontains=search_query)
    running_activities = running_activities.order_by('date', 'start_time').prefetch_related('laps')

    # Only the workout steps chosen on each activity's page count (by
    # default its runs; warm-up, recovery and cool-down don't).
    workout_data = []
    for activity in running_activities:
        totals = analysis_totals(sorted(activity.laps.all(), key=lambda lap: lap.lap_number), extra_types)
        if not totals or not totals['km']:
            continue
        date_str = activity.date.strftime('%Y-%m-%d')
        seconds = totals['seconds']
        workout_data.append({
            'date': date_str,
            'activity_id': activity.id,
            'activity_name': activity.name or f"Run {date_str}",
            'avg_pace': round(totals['pace'], 4) if totals['pace'] else None,
            'best_pace': round(totals['best_pace'], 4) if totals['best_pace'] else None,
            'avg_hr': totals['avg_hr'],
            'total_distance_km': round(totals['km'], 2),
            'total_time_seconds': seconds,
            'interval_count': totals['lap_count'],
            'avg_pace_formatted': _format_pace_min_per_km(totals['pace']) or '-',
            'best_pace_formatted': _format_pace_min_per_km(totals['best_pace']) or '-',
            'total_time_formatted': f"{seconds // 60}:{seconds % 60:02d}",
        })

    if workout_data:
        total_time_all = sum(w['total_time_seconds'] for w in workout_data)
        total_distance_all = sum(w['total_distance_km'] for w in workout_data)
        hr_workouts = [w for w in workout_data if w['avg_hr']]
        hr_seconds = sum(w['total_time_seconds'] for w in hr_workouts)
        all_paces = [w['avg_pace'] for w in workout_data if w['avg_pace']]
        overall_pace = (total_time_all / 60) / total_distance_all if total_distance_all else None
        overall_stats = {
            'total_workouts': len(workout_data),
            'total_intervals': sum(w['interval_count'] for w in workout_data),
            # Weighted like Garmin: total time / total distance, and heart
            # rate weighted by time - a short run and a long run don't count equally.
            'avg_pace': round(overall_pace, 4) if overall_pace else None,
            'best_avg_pace': round(min(all_paces), 4) if all_paces else None,
            'avg_hr': round(sum(w['avg_hr'] * w['total_time_seconds'] for w in hr_workouts) / hr_seconds) if hr_seconds else None,
            'total_distance': round(total_distance_all, 2),
            'avg_pace_formatted': _format_pace_min_per_km(overall_pace) or '-',
            'best_avg_pace_formatted': _format_pace_min_per_km(min(all_paces)) if all_paces else '-',
        }
    else:
        overall_stats = {
            'total_workouts': 0,
            'total_intervals': 0,
            'avg_pace': None,
            'best_avg_pace': None,
            'avg_hr': None,
            'total_distance': 0,
            'avg_pace_formatted': '-',
            'best_avg_pace_formatted': '-',
        }

    # Ensure all chart data is properly rounded to 2 decimals
    chart_data = {
        'labels': [w['date'] for w in workout_data],
        'pace_data': [round(w['avg_pace'], 2) if w['avg_pace'] else None for w in workout_data],
        'best_pace_data': [round(w['best_pace'], 2) if w['best_pace'] else None for w in workout_data],
        'hr_data': [round(w['avg_hr']) if w['avg_hr'] else None for w in workout_data],
        'distance_data': [round(w['total_distance_km'], 2) for w in workout_data],
        'interval_count_data': [w['interval_count'] for w in workout_data],
    }

    def build_filter_url(**overrides):
        params = {'type': workout_type, 'q': search_query,
                  'warmup': '1' if include_warmup else '', 'cooldown': '1' if include_cooldown else ''}
        params.update(overrides)
        query_string = urlencode({key: value for key, value in params.items() if value})
        return f"?{query_string}" if query_string else ""

    workout_filter_urls = {
        'all': build_filter_url(type='', q=''),
        'vk': build_filter_url(type='vk', q=''),
        'pk': build_filter_url(type='pk', q=''),
        'pitka': build_filter_url(type='pitka', q=''),
    }
    lap_filter_urls = {
        'warmup': build_filter_url(warmup='' if include_warmup else '1'),
        'cooldown': build_filter_url(cooldown='' if include_cooldown else '1'),
    }

    context = {
        'workout_data': workout_data,
        'overall_stats': overall_stats,
        'chart_data_json': json.dumps(chart_data),
        'search_query': search_query,
        'workout_type': workout_type,
        'workout_filter_urls': workout_filter_urls,
        'lap_filter_urls': lap_filter_urls,
        'include_warmup': include_warmup,
        'include_cooldown': include_cooldown,
    }
    
    return render(request, 'activities/running_analytics.html', context)
