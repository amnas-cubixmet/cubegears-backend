from django.urls import path
from .views import MyCompanyView, MyCompanyLogoView
from .onboarding import WorkshopSetupView, WorkshopSetupCompleteView
from .website_views import CompanyWebsiteSettingsView, CompanyWebsiteDomainsView, CompanyWebsiteDomainDetailView, CompanyWebsiteDomainCheckView, CompanyWebsiteDomainPrimaryView, PlatformCustomerDomainsView, PlatformCustomerDomainReviewView

urlpatterns = [
    path("me", MyCompanyView.as_view(), name="company-me"),
    path("onboarding", WorkshopSetupView.as_view(), name="company-onboarding"),
    path("onboarding/complete", WorkshopSetupCompleteView.as_view(), name="company-onboarding-complete"),
    path("logo", MyCompanyLogoView.as_view(), name="company-logo"),
    path("customer-website", CompanyWebsiteSettingsView.as_view(), name="company-customer-website"),
    path("customer-website/domains", CompanyWebsiteDomainsView.as_view(), name="company-customer-website-domains"),
    path("customer-website/domains/<int:domain_id>", CompanyWebsiteDomainDetailView.as_view(), name="company-customer-domain-detail"),
    path("customer-website/domains/<int:domain_id>/verify", CompanyWebsiteDomainCheckView.as_view(), name="company-customer-domain-verify"),
    path("customer-website/domains/<int:domain_id>/refresh", CompanyWebsiteDomainCheckView.as_view(), name="company-customer-domain-refresh"),
    path("customer-website/domains/<int:domain_id>/primary", CompanyWebsiteDomainPrimaryView.as_view(), name="company-customer-domain-primary"),
    path("platform/customer-domains", PlatformCustomerDomainsView.as_view(), name="platform-customer-domains"),
    path("platform/customer-domains/<int:domain_id>/review", PlatformCustomerDomainReviewView.as_view(), name="platform-customer-domain-review"),
]
