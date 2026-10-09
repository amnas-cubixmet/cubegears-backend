from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.employees.models import Employee
from apps.roles.models import Role
from .daily_wage_models import (
    DailyWageEntry, DailyWageExtra, EmployeeDailyWageRate,
    WageAdjustment, WageAuditLog, WagePayment, WagePaymentReversal,
)


class DailyWageLedgerTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Daily Wage Garage", slug="daily-wage-garage")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN")
        self.role = Role.objects.create(
            company=self.company, name="Payroll Admin", code="PAYROLL_ADMIN",
            permissions=["payroll.view", "payroll.edit", "attendance.manage"],
        )
        self.user = User.objects.create(
            company=self.company, branch=self.branch, role=self.role,
            name="Payroll Owner", email="owner@daily-wage.test",
        )
        self.employee = Employee.objects.create(
            company=self.company, branch=self.branch, name="Daily Mechanic",
            employee_code="DAY-001", payment_type="daily",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = f"/api/v1/payroll/daily-wages/employees/{self.employee.pk}"
        self.today = date.today()
        self.yesterday = self.today - timedelta(days=1)

    def setup_rate(self, value="800", when=None):
        return self.client.post(f"{self.url}/rates", {
            "rate": value, "effectiveFrom": str(when or self.yesterday),
            "reason": "Approved wage rate setup",
        }, format="json")

    def finalize(self, day, status="Full Day", reason="Manager approved"):
        return self.client.post(f"{self.url}/finalize", {
            "date": str(day), "status": status, "reason": reason,
        }, format="json")

    def account(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def test_full_half_absent_and_unpaid_leave_post_once(self):
        self.assertEqual(self.setup_rate().status_code, 201)
        self.assertEqual(self.finalize(self.yesterday, "Full Day").status_code, 200)
        self.assertEqual(self.finalize(self.yesterday, "Full Day").status_code, 200)
        self.assertEqual(self.finalize(self.today, "Half Day").status_code, 200)
        self.assertEqual(DailyWageEntry.objects.count(), 2)
        self.assertEqual(Decimal(self.account()["totalEarned"]), Decimal("1200"))
        self.assertEqual(self.finalize(self.today, "Absent").status_code, 200)
        self.assertEqual(Decimal(self.account()["totalEarned"]), Decimal("800"))
        self.assertEqual(self.finalize(self.today, "Unpaid Leave").status_code, 200)
        self.assertEqual(Decimal(self.account()["totalEarned"]), Decimal("800"))

    def test_pending_extras_approval_and_payment_are_idempotent(self):
        self.setup_rate("900")
        self.finalize(self.yesterday)
        extra = self.client.post(f"{self.url}/extras", {
            "date": str(self.yesterday), "category": "OT", "amount": "300",
            "reason": "Extra duty approved", "requestKey": "extra-001",
        }, format="json")
        self.assertEqual(extra.status_code, 201, extra.data)
        self.assertEqual(Decimal(self.account()["totalEarned"]), Decimal("900"))
        self.assertEqual(self.client.post(
            f"/api/v1/payroll/daily-wages/extras/{extra.data['id']}/approve",
            {"status": "Approved"}, format="json"
        ).status_code, 200)
        self.assertEqual(Decimal(self.account()["totalEarned"]), Decimal("1200"))
        pay = {"amount": "1200", "method": "UPI", "requestKey": "pay-001"}
        self.assertEqual(self.client.post(f"{self.url}/pay", pay, format="json").status_code, 200)
        self.assertEqual(self.client.post(f"{self.url}/pay", pay, format="json").status_code, 200)
        self.assertEqual(WagePayment.objects.count(), 1)
        self.assertEqual(Decimal(self.account()["currentBalance"]), Decimal("0"))
        self.finalize(self.today)
        self.assertEqual(Decimal(self.account()["currentBalance"]), Decimal("900"))

    def test_historical_rate_snapshot_and_settled_attendance_correction(self):
        self.setup_rate("800")
        self.finalize(self.yesterday)
        self.client.post(f"{self.url}/pay", {
            "amount": "800", "method": "Cash", "requestKey": "paid-old",
        }, format="json")
        self.setup_rate("900", self.today)
        self.finalize(self.today)
        self.assertEqual(
            list(DailyWageEntry.objects.order_by("work_date").values_list("applied_rate", flat=True)),
            [Decimal("800"), Decimal("900")]
        )
        self.finalize(self.yesterday, "Half Day", reason="Verified half-day correction")
        self.assertEqual(
            DailyWageEntry.objects.get(work_date=self.yesterday).base_amount,
            Decimal("800")
        )
        self.assertEqual(WageAdjustment.objects.count(), 1)
        self.assertEqual(WageAdjustment.objects.get().amount, Decimal("-400"))
        self.assertEqual(Decimal(self.account()["totalEarned"]), Decimal("1300"))
        self.assertTrue(WageAuditLog.objects.filter(action="ATTENDANCE_FINALIZED").exists())

    def test_partial_payment_reversal_and_overpayment_prevention(self):
        self.setup_rate("800")
        self.finalize(self.yesterday)
        payment = self.client.post(f"{self.url}/pay", {
            "amount": "200", "method": "Cash", "requestKey": "partial-01",
        }, format="json")
        self.assertEqual(payment.status_code, 200, payment.data)
        self.assertEqual(Decimal(self.account()["currentBalance"]), Decimal("600"))
        excess = self.client.post(f"{self.url}/pay", {
            "amount": "601", "method": "Cash", "requestKey": "invalid-02",
        }, format="json")
        self.assertEqual(excess.status_code, 400)
        reversal = self.client.post(
            f"/api/v1/payroll/daily-wages/payments/{payment.data['payment']['id']}/reverse",
            {"reason": "Wrong bank transfer"}, format="json",
        )
        self.assertEqual(reversal.status_code, 200, reversal.data)
        self.assertEqual(WagePaymentReversal.objects.count(), 1)
        self.assertEqual(Decimal(self.account()["currentBalance"]), Decimal("800"))

    def test_approved_attendance_edit_recalculates_unpaid_ledger_once(self):
        self.setup_rate("800")
        self.finalize(self.yesterday, "Full Day")
        entry = DailyWageEntry.objects.get(employee=self.employee, work_date=self.yesterday)
        updated = self.client.patch(
            f"/api/v1/attendance/records/{entry.attendance_id}",
            {"status": "Half Day", "reason": "Supervisor verified half-day"}, format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(Decimal(self.account()["totalEarned"]), Decimal("400"))
        self.assertEqual(DailyWageEntry.objects.count(), 1)

    def test_multiple_extras_same_day_are_distinct_and_do_not_duplicate(self):
        self.setup_rate("900")
        self.finalize(self.today)
        ids = []
        for i in range(2):
            result = self.client.post(f"{self.url}/extras", {
                "date": str(self.today), "category": "OD" if i else "OT",
                "amount": "300", "reason": f"Approved work {i}",
                "requestKey": f"extra-{i}",
            }, format="json")
            self.assertEqual(result.status_code, 201, result.data)
            ids.append(result.data["id"])
        self.assertEqual(len(set(ids)), 2)
        for extra_id in ids:
            reviewed = self.client.post(
                f"/api/v1/payroll/daily-wages/extras/{extra_id}/approve",
                {"status": "Approved"}, format="json",
            )
            self.assertEqual(reviewed.status_code, 200, reviewed.data)
        self.assertEqual(Decimal(self.account()["totalEarned"]), Decimal("1500"))
        retried = self.client.post(f"{self.url}/extras", {
            "date": str(self.today), "category": "OT", "amount": "300",
            "reason": "Approved work 0", "requestKey": "extra-0",
        }, format="json")
        self.assertEqual(retried.status_code, 200, retried.data)
        self.assertEqual(DailyWageExtra.objects.count(), 2)

    def test_unfinalized_attendance_does_not_post_wages(self):
        self.setup_rate("800")
        from apps.attendance.models import AttendanceRecord
        AttendanceRecord.objects.create(
            company=self.company, branch=self.branch, employee=self.employee,
            date=self.today, status="Present",
        )
        self.assertEqual(Decimal(self.account()["totalEarned"]), Decimal("0"))
        self.assertEqual(DailyWageEntry.objects.count(), 0)

    def test_rate_change_does_not_reprice_earlier_date_on_correction(self):
        self.setup_rate("800")
        self.finalize(self.yesterday)
        self.assertEqual(self.setup_rate("900", when=self.today).status_code, 201)
        self.finalize(self.yesterday, "Half Day")
        old = DailyWageEntry.objects.get(work_date=self.yesterday)
        self.assertEqual(old.applied_rate, Decimal("800"))
        self.assertEqual(old.base_amount, Decimal("400"))

    def test_legacy_monthly_run_does_not_create_duplicate_payslip(self):
        from .models import PayrollRun, Payslip
        from .services import process_payroll_run
        self.setup_rate("800")
        self.finalize(self.yesterday)
        run = PayrollRun.objects.create(
            company=self.company, branch=self.branch,
            month=self.today.month, year=self.today.year,
        )
        process_payroll_run(run, self.user)
        self.assertFalse(Payslip.objects.filter(employee=self.employee, payroll_run=run).exists())
        self.assertEqual(Decimal(self.account()["totalEarned"]), Decimal("800"))

    def test_cross_company_employees_are_not_visible(self):
        other_company = Company.objects.create(name="Other", slug="other-daily-wages")
        other_branch = Branch.objects.create(company=other_company, name="Other", code="OTHER")
        other = Employee.objects.create(
            company=other_company, branch=other_branch, name="Other",
            employee_code="DAY-OTHER",
        )
        self.assertEqual(self.client.get(
            f"/api/v1/payroll/daily-wages/employees/{other.pk}"
        ).status_code, 404)
        self.assertEqual(self.client.post(
            f"/api/v1/payroll/daily-wages/employees/{other.pk}/rates",
            {"rate": "999", "effectiveFrom": str(self.today), "reason": "No access"},
            format="json",
        ).status_code, 404)
