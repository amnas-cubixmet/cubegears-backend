from django.urls import path
from .portal_views import CustomerPortalDetailView, CustomerPortalLinkView

urlpatterns = [
    path("customer-portal/links/<uuid:customer_id>", CustomerPortalLinkView.as_view(), name="customer-portal-link"),
    path("c/<str:token>", CustomerPortalDetailView.as_view(), name="customer-portal-public"),
]
