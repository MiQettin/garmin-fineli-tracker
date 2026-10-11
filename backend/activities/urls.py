from django.urls import path
from .views import activities_view, activity_detail_view, running_analytics_view

urlpatterns = [
    path("", activities_view, name="activities"),
    path("<int:activity_id>/", activity_detail_view, name="activity_detail"),
    path("analytics/running/", running_analytics_view, name="running_analytics"),
]
