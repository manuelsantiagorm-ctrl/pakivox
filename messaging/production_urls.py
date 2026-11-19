from django.urls import path

from .production_views import health_live, health_ready


urlpatterns = [
    path("live/", health_live, name="health_live"),
    path("ready/", health_ready, name="health_ready"),
]
