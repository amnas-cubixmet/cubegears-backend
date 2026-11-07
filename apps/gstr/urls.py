from django.urls import path

from .views import GSTRReportView, GSTR2BReconcileView, GSTRCSVExportView
from .review_api import (
    GSTR2BHistory, GSTITCApprovals, GSTOutwardCreditNotes, GSTFilingReviews, GSTTaxLedger,
)

urlpatterns = [
    path("overview", GSTRReportView.as_view(), {"section": "overview"}),
    path("gstr-1", GSTRReportView.as_view(), {"section": "gstr-1"}),
    path("gstr-3b", GSTRReportView.as_view(), {"section": "gstr-3b"}),
    path("gstr-2b", GSTRReportView.as_view(), {"section": "gstr-2b"}),
    path("hsn-summary", GSTRReportView.as_view(), {"section": "hsn-summary"}),
    path("filing-status", GSTRReportView.as_view(), {"section": "filing-status"}),
    path("reconcile", GSTR2BReconcileView.as_view()),
    path("exports", GSTRCSVExportView.as_view()),
    path("imports", GSTR2BHistory.as_view()),
    path("itc", GSTITCApprovals.as_view()),
    path("credit-notes", GSTOutwardCreditNotes.as_view()),
    path("filing-review", GSTFilingReviews.as_view()),
    path("tax-ledger", GSTTaxLedger.as_view()),
]
