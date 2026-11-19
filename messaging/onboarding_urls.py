from django.urls import path

from . import onboarding_admin


urlpatterns = [
    path("", onboarding_admin.owner_onboarding_list, name="owner_onboarding_list"),
    path("<int:pk>/", onboarding_admin.owner_onboarding_detail, name="owner_onboarding_detail"),
    path("<int:pk>/estado/", onboarding_admin.owner_onboarding_transition, name="owner_onboarding_transition"),
]

