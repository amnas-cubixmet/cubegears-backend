from django.urls import path
from .daily_wage_views import (
    DailyWageDashboardView, DailyWageAccountView, DailyWageHistoryView,
    DailyWagePaymentListView, DailyWageRateView, DailyWageFinalizeView,
    DailyWageExtraView, DailyWageExtraApprovalView, DailyWageAdjustmentView,
    DailyWagePayView, DailyWageReversalView,
)

urlpatterns = [
    path("", DailyWageDashboardView.as_view(), name="daily-wages-dashboard"),
    path("history", DailyWageHistoryView.as_view(), name="daily-wages-history"),
    path("payments", DailyWagePaymentListView.as_view(), name="daily-wages-payments"),
    path("employees/<uuid:employee_id>", DailyWageAccountView.as_view(), name="daily-wages-account"),
    path("employees/<uuid:employee_id>/rates", DailyWageRateView.as_view(), name="daily-wages-rate"),
    path("employees/<uuid:employee_id>/finalize", DailyWageFinalizeView.as_view(), name="daily-wages-finalize"),
    path("employees/<uuid:employee_id>/extras", DailyWageExtraView.as_view(), name="daily-wages-extra"),
    path("employees/<uuid:employee_id>/adjustments", DailyWageAdjustmentView.as_view(), name="daily-wages-adjustment"),
    path("employees/<uuid:employee_id>/pay", DailyWagePayView.as_view(), name="daily-wages-pay"),
    path("extras/<uuid:extra_id>/approve", DailyWageExtraApprovalView.as_view(), name="daily-wages-extra-approve"),
    path("payments/<uuid:payment_id>/reverse", DailyWageReversalView.as_view(), name="daily-wages-payment-reverse"),
]
