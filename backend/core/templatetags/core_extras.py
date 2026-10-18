"""
Custom template tags and filters for activity/interval data.

Provides utility functions for calculating group statistics in lap/interval data.
"""

import json

from django import template
from django.utils.safestring import mark_safe
from django.utils.translation import gettext

register = template.Library()

def _format_pace_min_per_km(pace_min_per_km: float) -> str:
    """Format pace (min/km) as M:SS like Garmin Connect; "-" when unknown."""
    from activities.models import _format_pace_min_per_km as format_pace
    return format_pace(pace_min_per_km) or "-"


@register.filter
def get_lap_number(lap):
    """Get the lap number from a lap object."""
    return lap.lap_number


@register.filter
def get_cumulative_time(lap):
    """Get formatted cumulative time from a lap object."""
    return f"{lap.cumulative_time_seconds:.0f}s"


@register.simple_tag
def group_total_time(laps):
    """
    Calculate the total time for a group of laps.
    
    Args:
        laps: List of ActivityLap objects
        
    Returns:
        Formatted time string (HH:MM:SS or MM:SS)
    """
    if not laps:
        return "-"
    
    total_seconds = sum(lap.lap_time_seconds for lap in laps if lap.lap_time_seconds)
    
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    else:
        return f"{minutes:02d}:{seconds:02d}"


@register.simple_tag
def group_total_distance(laps):
    """
    Calculate the total distance for a group of laps.
    
    Args:
        laps: List of ActivityLap objects
        
    Returns:
        Formatted distance string in km
    """
    if not laps:
        return "-"
    
    total_distance = sum(lap.distance_km for lap in laps if lap.distance_km)
    return f"{total_distance:.2f}"


@register.simple_tag
def group_avg_pace(laps):
    """
    Calculate the average pace for a group of laps.
    
    Args:
        laps: List of ActivityLap objects
        
    Returns:
        Formatted pace string (MM:SS)
    """
    if not laps:
        return "-"

    total_seconds = sum(lap.lap_time_seconds for lap in laps if getattr(lap, "lap_time_seconds", None))
    total_distance_km = sum(lap.distance_km for lap in laps if getattr(lap, "distance_km", None))

    if not total_seconds or not total_distance_km:
        return "-"

    pace_min_per_km = (total_seconds / 60) / total_distance_km
    return _format_pace_min_per_km(pace_min_per_km)


@register.simple_tag
def group_avg_hr(laps):
    """
    Calculate the average heart rate for a group of laps.
    
    Args:
        laps: List of ActivityLap objects
        
    Returns:
        Average HR as integer or "-"
    """
    laps_with_hr = [lap for lap in laps if lap.avg_hr]
    
    if not laps_with_hr:
        return "-"
    
    avg_hr = sum(lap.avg_hr for lap in laps_with_hr) / len(laps_with_hr)
    return f"{int(avg_hr)}"


@register.simple_tag
def group_max_hr(laps):
    """
    Get the maximum heart rate across a group of laps.
    
    Args:
        laps: List of ActivityLap objects
        
    Returns:
        Max HR as integer or "-"
    """
    laps_with_hr = [lap for lap in laps if lap.max_hr]
    
    if not laps_with_hr:
        return "-"
    
    max_hr = max(lap.max_hr for lap in laps_with_hr)
    return str(int(max_hr))


@register.simple_tag
def group_total_ascent(laps):
    """
    Calculate the total ascent for a group of laps.
    
    Args:
        laps: List of ActivityLap objects
        
    Returns:
        Total ascent in meters as integer or "-"
    """
    laps_with_ascent = [lap for lap in laps if lap.total_ascent_m]
    
    if not laps_with_ascent:
        return "-"
    
    total_ascent = sum(lap.total_ascent_m for lap in laps_with_ascent)
    return f"{int(total_ascent)}"


@register.simple_tag
def group_avg_cadence(laps):
    """
    Calculate the average cadence for a group of laps.
    
    Args:
        laps: List of ActivityLap objects
        
    Returns:
        Average cadence as integer or "-"
    """
    laps_with_cadence = [lap for lap in laps if lap.avg_cadence]
    
    if not laps_with_cadence:
        return "-"
    
    avg_cadence = sum(lap.avg_cadence for lap in laps_with_cadence) / len(laps_with_cadence)
    return f"{int(avg_cadence)}"


@register.filter
def get_item(dictionary, key):
    """Get an item from a dictionary using a key"""
    if dictionary is None:
        return None
    return dictionary.get(key)


@register.filter
def activity_type_label(code):
    """Activity type in the app language: {{ activity.activity_type|activity_type_label }}."""
    from activities.labels import activity_type_label as label
    return label(code)


@register.filter
def step_type_label(code):
    """Lap step type in the app language: {{ lap.step_type|step_type_label }}."""
    from activities.labels import step_type_label as label
    return label(code)


@register.simple_tag
def jstrans(text):
    """A translated text as a JavaScript string literal, quotes included:
    `const msg = {% jstrans "Search failed" %};`. Placeholders such as
    {name} are left for the script to fill in. Safe inside <script>."""
    literal = json.dumps(gettext(text))
    return mark_safe(literal.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))
