from django.urls import path
from .views import LegacySignupRolesCleanupView, RoleDetailView, RoleListCreateView

urlpatterns = [
    path("", RoleListCreateView.as_view(), name="role-list"),
    path("legacy-presets/", LegacySignupRolesCleanupView.as_view(), name="role-legacy-presets"),
    path("<uuid:pk>", RoleDetailView.as_view(), name="role-detail"),
]
