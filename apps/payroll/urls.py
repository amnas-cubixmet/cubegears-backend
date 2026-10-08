from rest_framework.routers import DefaultRouter
from .work_session_views import JobWorkSessionViewSet
from .job_timer_assignments import JobTimerAssignmentsViewSet

from .views import (
    CommissionRuleViewSet,
    CompensationComponentViewSet,
    EmployeeCommissionViewSet,
    EmployeeCompensationPlanViewSet,
    EmployeeWorkLogViewSet,
    IncentiveViewSet,
    JobCardEmployeeAssignmentViewSet,
    PayrollAdjustmentViewSet,
    PayrollLineItemViewSet,
    PayrollPeriodViewSet,
    PayrollPolicyViewSet,
    PayrollRunViewSet,
    PayslipViewSet,
    SalaryAdvanceViewSet,
    SalaryPaymentViewSet,
    SalaryStructureViewSet,
)

router=DefaultRouter(trailing_slash=False)
router.register("policy",PayrollPolicyViewSet,basename="payroll-policy")
router.register("compensation-plans",EmployeeCompensationPlanViewSet,basename="compensation-plans")
router.register("compensation-components",CompensationComponentViewSet,basename="compensation-components")
router.register("commission-rules",CommissionRuleViewSet,basename="commission-rules")
router.register("job-assignments",JobCardEmployeeAssignmentViewSet,basename="job-assignments")
router.register("work-logs",EmployeeWorkLogViewSet,basename="work-logs")
router.register("work-sessions",JobWorkSessionViewSet,basename="work-sessions")
router.register("job-timer-assignments",JobTimerAssignmentsViewSet,basename="job-timer-assignments")
router.register("commissions",EmployeeCommissionViewSet,basename="employee-commissions")
router.register("salary-setup",SalaryStructureViewSet,basename="salary-setup")
router.register("advances",SalaryAdvanceViewSet,basename="salary-advances")
router.register("periods",PayrollPeriodViewSet,basename="payroll-periods")
router.register("runs",PayrollRunViewSet,basename="payroll-runs")
router.register("payslips",PayslipViewSet,basename="payslips")
router.register("line-items",PayrollLineItemViewSet,basename="payroll-line-items")
router.register("adjustments",PayrollAdjustmentViewSet,basename="payroll-adjustments")
router.register("salary-payments",SalaryPaymentViewSet,basename="salary-payments")
router.register("incentives",IncentiveViewSet,basename="incentives")

urlpatterns=router.urls
