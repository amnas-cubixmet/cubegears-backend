from rest_framework.routers import DefaultRouter
from .views import SalaryStructureViewSet,SalaryAdvanceViewSet,PayrollRunViewSet,PayslipViewSet
router=DefaultRouter(trailing_slash=False)
router.register("salary-setup",SalaryStructureViewSet,basename="salary-setup")
router.register("advances",SalaryAdvanceViewSet,basename="salary-advances")
router.register("runs",PayrollRunViewSet,basename="payroll-runs")
router.register("payslips",PayslipViewSet,basename="payslips")
urlpatterns=router.urls
