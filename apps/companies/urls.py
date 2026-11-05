from django.urls import path
from .views import MyCompanyView, MyCompanyLogoView
from .website_views import CompanyWebsiteSettingsView, CompanyWebsiteDomainsView, CompanyWebsiteDomainDetailView

urlpatterns = [
    path("me", MyCompanyView.as_view(), name="company-me"),
    path("logo", MyCompanyLogoView.as_view(), name="company-logo"),
    path("customer-website", CompanyWebsiteSettingsView.as_view(), name="company-customer-website"),
    path("customer-website/domains", CompanyWebsiteDomainsView.as_view(), name="company-customer-website-domains"),
    path("customer-website/domains/<int:domain_id>", CompanyWebsiteDomainDetailView.as_view(), name="company-customer-domain-detail"),
]
