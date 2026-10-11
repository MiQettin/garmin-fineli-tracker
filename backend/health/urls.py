from django.urls import path

from .views import health_analytics_view

urlpatterns = [
    path("analytics/", health_analytics_view, name="health_analytics"),
]
