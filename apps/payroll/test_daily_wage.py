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
            permissions=["payroll.view", "payroll.edit", "attendance.manage", "staff.create"],
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

    def test_all_leave_types_and_weekly_off_pay_zero_without_duplicates(self):
        self.setup_rate("800")
        unpaid_statuses = [
            "Leave", "Full Day Leave", "On Leave", "Paid Leave",
            "Sick Leave", "Casual Leave", "Unpaid Leave",
            "Absent", "Weekly Off", "Weekly Off (Not Worked)", "Holiday",
        ]
        for status in unpaid_statuses:
            with self.subTest(attendance=status):
                result = self.finalize(self.today, status)
                self.assertEqual(result.status_code, 200, result.data)
                self.assertEqual(Decimal(result.data["base"]), Decimal("0"))
                self.assertEqual(Decimal(self.account()["totalEarned"]), Decimal("0"))
                self.assertEqual(Decimal(self.account()["currentBalance"]), Decimal("0"))
                self.assertEqual(DailyWageEntry.objects.count(), 1)

    def test_worked_full_and_half_days_use_independent_rate(self):
        self.setup_rate("800")
        full = self.finalize(self.yesterday, "Full Day Present")
        half = self.finalize(self.today, "Half Day Worked")
        self.assertEqual(full.status_code, 200, full.data)
        self.assertEqual(half.status_code, 200, half.data)
        self.assertEqual(Decimal(full.data["base"]), Decimal("800"))
        self.assertEqual(Decimal(half.data["base"]), Decimal("400"))
        self.assertEqual(Decimal(self.account()["totalEarned"]), Decimal("1200"))

    def test_approved_full_day_leave_blocks_wage_even_if_labeled_paid(self):
        from apps.attendance.models import LeaveRequest
        self.setup_rate("800")
        LeaveRequest.objects.create(
            company=self.company, branch=self.branch, employee=self.employee,
            leave_type="Paid Leave", start_date=self.today, end_date=self.today,
            status="Approved", reason="Approved day of leave",
        )
        no_salary = self.finalize(self.today, "Paid Leave")
        self.assertEqual(no_salary.status_code, 200, no_salary.data)
        self.assertEqual(Decimal(self.account()["totalEarned"]), Decimal("0"))
        incorrectly_marked_worked = self.finalize(self.today, "Full Day")
        self.assertEqual(incorrectly_marked_worked.status_code, 400, incorrectly_marked_worked.data)
        self.assertEqual(Decimal(self.account()["totalEarned"]), Decimal("0"))

    def test_half_day_approved_leave_allows_only_worked_half_day_wage(self):
        from apps.attendance.models import LeaveRequest
        self.setup_rate("800")
        LeaveRequest.objects.create(
            company=self.company, branch=self.branch, employee=self.employee,
            leave_type="Unpaid Leave", start_date=self.today, end_date=self.today,
            half_day=True, status="Approved", reason="Half-day unpaid leave",
        )
        worked = self.finalize(self.today, "Half Day Worked")
        self.assertEqual(worked.status_code, 200, worked.data)
        self.assertEqual(Decimal(worked.data["base"]), Decimal("400"))
        self.assertEqual(self.finalize(self.today, "Full Day").status_code, 400)

    def test_overtime_adds_to_worked_wage_then_full_payment_zeros_balance(self):
        self.setup_rate("800")
        self.finalize(self.today, "Full Day")
        extra = self.client.post(f"{self.url}/extras", {
            "date": str(self.today), "category": "OT", "amount": "200",
            "reason": "Approved two-hour overtime", "requestKey": "ot-200",
        }, format="json")
        self.assertEqual(extra.status_code, 201, extra.data)
        reviewed = self.client.post(
            f"/api/v1/payroll/daily-wages/extras/{extra.data['id']}/approve",
            {"status": "Approved"}, format="json",
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.data)
        self.assertEqual(Decimal(self.account()["currentBalance"]), Decimal("1000"))
        paid = self.client.post(f"{self.url}/pay", {
            "amount": "1000", "method": "UPI", "requestKey": "settle-1000",
        }, format="json")
        self.assertEqual(paid.status_code, 200, paid.data)
        self.assertEqual(Decimal(self.account()["currentBalance"]), Decimal("0"))
        self.assertEqual(Decimal(self.account()["totalEarned"]), Decimal("1000"))
        self.assertEqual(Decimal(self.account()["totalPaid"]), Decimal("1000"))

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

    def test_partial_payments_allocate_oldest_workdays_first(self):
        from .daily_wage_models import WagePaymentAllocation
        self.setup_rate("800")
        self.finalize(self.yesterday)
        self.finalize(self.today)
        first = self.client.post(f"{self.url}/pay", {
            "amount": "1000", "method": "UPI", "requestKey": "fifo-one",
        }, format="json")
        self.assertEqual(first.status_code, 200, first.data)
        allocations = list(WagePaymentAllocation.objects.order_by("work_date"))
        self.assertEqual([row.work_date for row in allocations], [self.yesterday, self.today])
        self.assertEqual([row.amount for row in allocations], [Decimal("800"), Decimal("200")])
        entries = {x["date"]: x for x in self.account()["history"]}
        self.assertEqual(entries[str(self.yesterday)]["paymentStatus"], "Paid")
        self.assertEqual(entries[str(self.today)]["paymentStatus"], "Part Paid")
        second = self.client.post(f"{self.url}/pay", {
            "amount": "600", "method": "Cash", "requestKey": "fifo-two",
        }, format="json")
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(Decimal(self.account()["currentBalance"]), Decimal("0"))
        entries = {x["date"]: x for x in self.account()["history"]}
        self.assertEqual(entries[str(self.today)]["paymentStatus"], "Paid")

    def test_attendance_manager_compat_updates_posted_day_without_duplicates(self):
        self.setup_rate("800")
        self.finalize(self.yesterday)
        entry = DailyWageEntry.objects.get(employee=self.employee, work_date=self.yesterday)
        edited = self.client.put(
            f"/api/v1/attendance-manager/team/{entry.attendance_id}",
            {"status": "Half Day", "auditReason": "Approved by attendance manager"},
            format="json",
        )
        self.assertEqual(edited.status_code, 200, edited.data)
        self.assertEqual(DailyWageEntry.objects.count(), 1)
        self.assertEqual(Decimal(self.account()["totalEarned"]), Decimal("400"))

    def test_legacy_salary_frequency_and_commission_writes_are_retired(self):
        # Old reports remain available, but creating new monthly/weekly
        # earnings through obsolete endpoints must be impossible.
        for url in (
            "/api/v1/payroll/policy",
            "/api/v1/payroll/compensation-plans",
            "/api/v1/payroll/commission-rules",
            "/api/v1/payroll/commissions",
            "/api/v1/payroll/salary-setup",
            "/api/v1/payroll/runs",
            "/api/v1/payroll/payslips",
            "/api/v1/payroll/adjustments",
        ):
            with self.subTest(url=url):
                response = self.client.post(url, {
                    "paymentType": "monthly", "paymentFrequency": "weekly",
                }, format="json")
                self.assertIn(response.status_code, (403, 405), response.data)

    def test_employees_cannot_set_monthly_pay_type_on_create(self):
        result = self.client.post("/api/v1/employees", {
            "name": "New Daily Worker", "paymentType": "monthly",
        }, format="json")
        self.assertEqual(result.status_code, 201, result.data)
        created = Employee.objects.get(pk=result.data["id"])
        self.assertEqual(created.payment_type, "daily")
        self.assertNotIn("paymentType", result.data)

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
