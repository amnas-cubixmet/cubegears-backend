from django.urls import path
from . import views
from .payment_activity import FinancePaymentActivity

urlpatterns = [
    path("payment-activity", FinancePaymentActivity.as_view(), name="finance-payment-activity"),
    path("dashboard", views.FinanceDashboard.as_view(), name="finance-dashboard"),
    path("accounts", views.FinanceAccountCollection.as_view(), name="finance-accounts"),
    path("accounts/<uuid:pk>", views.FinanceAccountDetail.as_view(), name="finance-account-detail"),
    path("accounts/<uuid:pk>/adjust", views.FinanceAdjustment.as_view(), name="finance-account-adjust"),
    path("transfers", views.FinanceTransferCollection.as_view(), name="finance-transfers"),
    path("ledger", views.FinanceLedger.as_view(), name="finance-ledger"),
    path("reports", views.FinanceReports.as_view(), name="finance-reports"),
]
