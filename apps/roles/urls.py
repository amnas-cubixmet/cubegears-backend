from django.urls import path
from .views import RoleDetailView, RoleListCreateView

urlpatterns = [
    path("", RoleListCreateView.as_view(), name="role-list"),
    path("<uuid:pk>", RoleDetailView.as_view(), name="role-detail"),
]
