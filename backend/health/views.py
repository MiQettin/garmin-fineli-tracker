"""Thin HTTP layer exposing health.analytics.get_health_analytics() as JSON.

No calculations happen here - this view only parses/validates end_date and
the optional start_date, calls the tested analytics function, and converts
date/datetime values to JSON-safe ISO strings.
"""

from datetime import date, datetime

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .analytics import get_health_analytics


def _json_safe(value):
    """Recursively convert date/datetime values to ISO strings; leave everything else as-is."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


@require_GET
def health_analytics_view(request):
    # Manual check (not @login_required): called via fetch() from
    # health_page.js, which expects a JSON body (it reads `body.error` on a
    # non-ok response) - a login-page redirect would break that parsing.
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    end_date_param = request.GET.get("end_date")

    if end_date_param:
        try:
            end_date = date.fromisoformat(end_date_param)
        except ValueError:
            return JsonResponse(
                {"error": f"Invalid end_date '{end_date_param}'. Use YYYY-MM-DD."},
                status=400,
            )
    else:
        end_date = timezone.localdate()

    start_date_param = request.GET.get("start_date")
    start_date = None
    if start_date_param:
        try:
            start_date = date.fromisoformat(start_date_param)
        except ValueError:
            return JsonResponse(
                {"error": f"Invalid start_date '{start_date_param}'. Use YYYY-MM-DD."},
                status=400,
            )
        if start_date > end_date:
            return JsonResponse(
                {"error": "start_date must be on or before end_date."},
                status=400,
            )

    analytics = get_health_analytics(end_date, start_date=start_date)

    return JsonResponse({
        "end_date": end_date.isoformat(),
        "start_date": start_date.isoformat() if start_date else None,
        "analytics": _json_safe(analytics),
    })


@login_required
def health_page_view(request):
    """Render the Health/Recovery page shell. Data loads client-side from
    health_analytics_view via fetch() - this view performs no analytics
    queries of its own."""
    return render(request, "health/health_page.html")
