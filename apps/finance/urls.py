from django.urls import path
from . import views
from .payment_activity import FinancePaymentActivity
from .accounting_api import (
    AccountingStatements, BankReconciliations, BankReconciliationCandidates,
    ChartOfAccounts, GeneralJournal,
)

urlpatterns = [
    path("payment-activity", FinancePaymentActivity.as_view(), name="finance-payment-activity"),
    path("dashboard", views.FinanceDashboard.as_view(), name="finance-dashboard"),
    path("accounts", views.FinanceAccountCollection.as_view(), name="finance-accounts"),
    path("accounts/<uuid:pk>", views.FinanceAccountDetail.as_view(), name="finance-account-detail"),
    path("accounts/<uuid:pk>/adjust", views.FinanceAdjustment.as_view(), name="finance-account-adjust"),
    path("transfers", views.FinanceTransferCollection.as_view(), name="finance-transfers"),
    path("ledger", views.FinanceLedger.as_view(), name="finance-ledger"),
    path("reports", views.FinanceReports.as_view(), name="finance-reports"),
    path("chart", ChartOfAccounts.as_view(), name="finance-chart"),
    path("journals", GeneralJournal.as_view(), name="finance-journals"),
    path("statements/<str:report>", AccountingStatements.as_view(), name="finance-statements"),
    path("bank-reconciliations", BankReconciliations.as_view(), name="finance-bank-reconciliations"),
    path("bank-reconciliations/candidates", BankReconciliationCandidates.as_view(), name="finance-bank-candidates"),
]
