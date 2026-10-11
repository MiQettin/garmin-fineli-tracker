from .models import UserSettings


def garmin_sync_status(request):
    """Makes last_garmin_sync available in the nav (base.html) on every
    page, not just the dashboard, since the Sync Garmin button now lives
    in the shared nav."""
    if not request.user.is_authenticated:
        return {}
    return {"last_garmin_sync": UserSettings.get_settings().last_garmin_sync}
