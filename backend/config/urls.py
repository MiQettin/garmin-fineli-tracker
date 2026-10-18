"""
URL configuration for config project.
"""
from django.contrib import admin
from django.urls import path, include
from activities.views import running_analytics_view
from health.views import health_page_view
from health.weight_views import weight_delete_view, weight_edit_view, weight_page_view


urlpatterns = [
    path("admin/", admin.site.urls),  # also the application's login/logout entry point

    # App routes
    path("", include("core.urls")),                          # Dashboard, Settings
    path("activities/", include("activities.urls")),         # Activities list
    path("analytics/running/", running_analytics_view, name="running_analytics"),  # Top-level analytics
    path("food-diary/", include("nutrition.urls")),          # Food diary, Fineli
    path("health/", health_page_view, name="health_page"),   # Health / Recovery page
    path("health/weight/", weight_page_view, name="weight_page"),  # Weight history
    path("health/weight/<int:entry_id>/edit/", weight_edit_view, name="weight_edit"),
    path("health/weight/<int:entry_id>/delete/", weight_delete_view, name="weight_delete"),
    path("api/health/", include("health.urls")),             # Health analytics API
]

