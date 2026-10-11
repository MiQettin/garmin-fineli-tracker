from django.urls import path
from .views import week_view, settings_view, sync_garmin

urlpatterns = [
    path("", week_view, name="week_view"),
    path("settings/", settings_view, name="settings"),
    path("sync-garmin/", sync_garmin, name="sync_garmin"),
]
