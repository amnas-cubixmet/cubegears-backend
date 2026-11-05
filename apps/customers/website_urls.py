from django.urls import path
from .website_views import (
    WebsiteBrandingView, WebsiteSessionView, WebsiteLogoutView,
    WebsiteLogoView, WebsitePhotoView,
)

urlpatterns = [
    path("branding", WebsiteBrandingView.as_view(), name="customer-website-branding"),
    path("session", WebsiteSessionView.as_view(), name="customer-website-session"),
    path("logout", WebsiteLogoutView.as_view(), name="customer-website-logout"),
    path("logo", WebsiteLogoView.as_view(), name="customer-website-logo"),
    path("photos/<uuid:photo_id>", WebsitePhotoView.as_view(), name="customer-website-photo"),
]
