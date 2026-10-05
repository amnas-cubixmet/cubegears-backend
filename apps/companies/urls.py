from django.urls import path
from .views import MyCompanyView

urlpatterns = [
    path("me", MyCompanyView.as_view(), name="company-me"),
]
