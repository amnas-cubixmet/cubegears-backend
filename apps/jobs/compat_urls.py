from django.urls import path
from .compat_views import *
urlpatterns=[
    path("job-cards/<uuid:job_id>/parts/availability",PartsAvailabilityView.as_view()),
    path("job-cards/<uuid:job_id>/issues",JobIssuesView.as_view()),
    path("job-cards/<uuid:job_id>/returns",JobReturnsView.as_view()),
    path("job-cards/<uuid:job_id>/completion-eligibility",CompletionEligibilityView.as_view()),
    path("job-cards/<uuid:job_id>/parts/transactions",PartsTransactionsView.as_view()),
    path("job-cards/<uuid:job_id>/purchase-orders",JobPurchaseOrderView.as_view()),
    path("purchase-orders/<str:po_id>/inwards",PurchaseOrderInwardView.as_view()),
]
