from django.urls import path
from .views import MyCompanyView, MyCompanyLogoView

urlpatterns = [
    path("me", MyCompanyView.as_view(), name="company-me"),
    path("logo", MyCompanyLogoView.as_view(), name="company-logo"),
]
