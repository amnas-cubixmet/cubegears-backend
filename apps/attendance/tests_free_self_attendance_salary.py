"""Self attendance and private salary payments stay available without the paid HR bundle."""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.companies.models import Company
from apps.employees.models import Employee
from apps.payroll.models import PayrollRun, Payslip, SalaryPayment, WagePayment, WagePaymentReversal
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=True,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=True,
    CREDIT_STRICT_MODULE_GATING_ENABLED=True,
    CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=False,
)
class StaffSelfAttendanceAndSalaryTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Attendance Free Workshop", slug="attendance-free-workshop",
            require_paid_features=True,
        )
        self.role = Role.objects.create(
            company=self.company, name="Mechanic", code="FREE_STAFF",
            permissions=[], is_active=True,
        )
        self.user = User.objects.create_user(
            name="Staff A", email="self-staff@workshop.test",
            password="StaffPassword123!", company=self.company, role=self.role,
        )
        self.employee = Employee.objects.create(
            company=self.company, user=self.user,
            name="Staff A", employee_code="STAFF-FREE-01", status="Active",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_free_self_attendance_without_purchase_does_not_unlock_hr_management(self):
        self.assertTrue(user_has_permission(self.user, "attendance.self"))
        for code in ("attendance.team.view", "attendance.records.manage",
                     "attendance.rules.manage", "payroll.view",
                     "payroll.payments.record", "payroll.payments.view"):
            self.assertFalse(user_has_permission(self.user, code), code)
        access = self.client.get("/api/v1/saas/credits/access")
        self.assertEqual(access.status_code, 200, access.data)
        self.assertIn("attendance.self", access.data["permissions"])
        self.assertNotIn("attendance.team.view", access.data["permissions"])
        self.assertNotIn("payroll.view", access.data["permissions"])
        self.assertNotIn("attendance", access.data["enabledModules"])
        self.assertNotIn("payroll", access.data["enabledModules"])
        self.assertEqual(self.client.get("/api/v1/my-attendance/logs").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/my-attendance/salary").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/payroll/salary-payments").status_code, 403)

    def test_only_own_paid_salary_and_unreversed_daily_wages_are_readable(self):
        other = User.objects.create_user(
            name="Staff B", email="other-staff@workshop.test",
            password="StaffPassword123!", company=self.company, role=self.role,
        )
        other_employee = Employee.objects.create(
            company=self.company, user=other,
            name="Staff B", employee_code="STAFF-FREE-02", status="Active",
        )
        run = PayrollRun.objects.create(
            company=self.company, month=10, year=2026,
            status="Approved", approval_status="Approved",
        )
        own_slip = Payslip.objects.create(
            company=self.company, payroll_run=run, employee=self.employee,
            net=Decimal("1500.00"), paid_amount=Decimal("1000.00"),
            payment_status="Partial", status="Approved",
        )
        other_slip = Payslip.objects.create(
            company=self.company, payroll_run=run, employee=other_employee,
            net=Decimal("9999.00"), paid_amount=Decimal("9999.00"),
            payment_status="Paid", status="Approved",
        )
        SalaryPayment.objects.create(
            company=self.company, payslip=own_slip, employee=self.employee,
            payment_date=date(2026, 11, 2), amount=Decimal("1000.00"),
            method="Bank Transfer", reference="OWN-TRANSFER", status="Successful",
        )
        SalaryPayment.objects.create(
            company=self.company, payslip=other_slip, employee=other_employee,
            payment_date=date(2026, 11, 3), amount=Decimal("9999.00"),
            method="Cash", reference="OTHER-SECRET", status="Successful",
        )
        WagePayment.objects.create(
            company=self.company, employee=self.employee,
            payment_date=date(2026, 11, 1), amount=Decimal("300.00"),
            method="Cash", reference="OWN-WAGE", request_key="own-wage-1",
        )
        reversed_wage = WagePayment.objects.create(
            company=self.company, employee=self.employee,
            payment_date=date(2026, 10, 30), amount=Decimal("500.00"),
            method="Cash", reference="REVERSED", request_key="own-wage-2",
        )
        WagePaymentReversal.objects.create(
            company=self.company, payment=reversed_wage,
            reason="Invalid payment",
        )
        data = self.client.get("/api/v1/my-attendance/salary")
        self.assertEqual(data.status_code, 200, data.data)
        self.assertEqual(len(data.data), 2)
        self.assertEqual({row["reference"] for row in data.data}, {"OWN-WAGE", "OWN-TRANSFER"})
        self.assertEqual({row["type"] for row in data.data}, {"Salary", "Daily wage"})
        self.assertNotIn("9999.00", str(data.data))
        self.assertNotIn("OTHER-SECRET", str(data.data))
        self.assertNotIn("REVERSED", str(data.data))
        self.assertEqual(self.client.post("/api/v1/my-attendance/salary", {}).status_code, 405)

    def test_inactive_employee_or_revoked_login_cannot_use_self_attendance(self):
        self.employee.status = "Inactive"
        self.employee.save(update_fields=["status"])
        self.assertFalse(user_has_permission(self.user, "attendance.self"))
        self.assertEqual(self.client.get("/api/v1/my-attendance/salary").status_code, 403)
        self.employee.status = "Active"
        self.employee.save(update_fields=["status"])
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertFalse(user_has_permission(self.user, "attendance.self"))

    def test_other_company_staff_cannot_access_this_company_salary(self):
        foreign = Company.objects.create(name="Foreign Workshop", slug="foreign-attendance-salary")
        foreign_role = Role.objects.create(
            company=foreign, name="Mechanic", code="FOREIGN_STAFF",
            permissions=[], is_active=True,
        )
        foreign_user = User.objects.create_user(
            name="Foreign Staff", email="foreign-salary@workshop.test",
            password="StaffPassword123!", company=foreign, role=foreign_role,
        )
        Employee.objects.create(
            company=foreign, user=foreign_user,
            name="Foreign Staff", employee_code="FOREIGN-01", status="Active",
        )
        self.client.force_authenticate(foreign_user)
        self.assertEqual(self.client.get("/api/v1/my-attendance/salary").data, [])
