from django.urls import path
from .portal_views import CustomerPortalDetailView, CustomerPortalLinkView, CustomerPortalLogoView

urlpatterns = [
    path("customer-portal/links/<uuid:customer_id>", CustomerPortalLinkView.as_view(), name="customer-portal-link"),
    path("c/<str:token>/logo", CustomerPortalLogoView.as_view(), name="customer-portal-logo"),
    path("c/<str:token>", CustomerPortalDetailView.as_view(), name="customer-portal-public"),
]
