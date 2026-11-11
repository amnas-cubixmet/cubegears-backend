from django.urls import path
from .views import MyCompanyView, MyCompanyLogoView
from .onboarding import WorkshopSetupView, WorkshopSetupCompleteView
from .website_views import CompanyWebsiteSettingsView, CompanyWebsiteSubdomainView

urlpatterns = [
    path("me", MyCompanyView.as_view(), name="company-me"),
    path("onboarding", WorkshopSetupView.as_view(), name="company-onboarding"),
    path("onboarding/complete", WorkshopSetupCompleteView.as_view(), name="company-onboarding-complete"),
    path("logo", MyCompanyLogoView.as_view(), name="company-logo"),
    path("customer-website", CompanyWebsiteSettingsView.as_view(), name="company-customer-website"),
    path("customer-website/subdomain", CompanyWebsiteSubdomainView.as_view(), name="company-customer-website-subdomain"),
]
