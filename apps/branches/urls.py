from django.urls import path
from .views import BranchDetailView, BranchListCreateView

urlpatterns = [
    path("", BranchListCreateView.as_view(), name="branch-list"),
    path("<uuid:pk>", BranchDetailView.as_view(), name="branch-detail"),
]
