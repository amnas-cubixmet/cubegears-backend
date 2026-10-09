from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.employees.models import Employee
from apps.roles.models import Role
from .models import LeaveRequest, LeaveType


class NoPaidLeaveTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="No Paid Leave Workshop", slug="no-paid-leave-workshop")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN")
        role = Role.objects.create(
            company=self.company, name="Attendance Manager", code="ATTENDANCE_MGR",
            permissions=["attendance.self", "attendance.manage"],
        )
        self.user = User.objects.create(
            company=self.company, branch=self.branch, role=role,
            name="Mechanic Manager", email="mechanic@nopaidleave.test",
        )
        self.worker = Employee.objects.create(
            company=self.company, branch=self.branch, user=self.user,
            name="Mechanic", employee_code="W-001", payment_type="daily",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.today = timezone.localdate()

    def test_leave_type_selection_is_unnecessary_and_every_new_leave_is_unpaid(self):
        request = {
            "startDate": str(self.today + timedelta(days=1)),
            "endDate": str(self.today + timedelta(days=1)),
            "halfDay": False,
            "reason": "Family commitment",
        }
        submitted = self.client.post("/api/v1/leave/requests", request, format="json")
        self.assertEqual(submitted.status_code, 201, submitted.data)
        entry = LeaveRequest.objects.get(pk=submitted.data["id"])
        self.assertEqual(entry.leave_type, "Unpaid Leave")
        self.assertEqual(submitted.data["payType"], "Unpaid")
        self.assertFalse(submitted.data["isPaid"])

    def test_old_client_cannot_create_paid_leave_even_when_requested(self):
        tomorrow = str(self.today + timedelta(days=2))
        submitted = self.client.post("/api/v1/leave/requests", {
            "leaveType": "Annual Paid Leave",
            "startDate": tomorrow, "reason": "Test old client",
        }, format="json")
        self.assertEqual(submitted.status_code, 201, submitted.data)
        self.assertEqual(LeaveRequest.objects.get(pk=submitted.data["id"]).leave_type, "Unpaid Leave")

        generic = self.client.post("/api/v1/attendance/leave", {
            "employee": str(self.worker.id),
            "leave_type": "Sick Paid Leave",
            "start_date": str(self.today + timedelta(days=3)),
            "end_date": str(self.today + timedelta(days=3)),
            "reason": "Test generic endpoint",
        }, format="json")
        self.assertEqual(generic.status_code, 201, generic.data)
        self.assertEqual(LeaveRequest.objects.get(pk=generic.data["id"]).leave_type, "Unpaid Leave")

    def test_paid_leave_configuration_is_inaccessible_but_legacy_history_is_preserved(self):
        LeaveType.objects.create(
            company=self.company, branch=self.branch, name="Historic Paid Leave", code="PL",
            leave_type="Paid",
        )
        historical = LeaveRequest.objects.create(
            company=self.company, branch=self.branch, employee=self.worker,
            leave_type="Historic Paid Leave", start_date=self.today, end_date=self.today,
            reason="Existing record",
            status="Approved",
        )
        balances = self.client.get("/api/v1/leave/balances")
        self.assertEqual(balances.status_code, 200, balances.data)
        self.assertEqual(len(balances.data), 1)
        self.assertTrue(balances.data[0]["isUnpaid"])
        self.assertFalse(balances.data[0]["isPaid"])
        rows = self.client.get("/api/v1/leave/requests")
        self.assertEqual(rows.status_code, 200, rows.data)
        item = next(row for row in rows.data if str(row["id"]) == str(historical.id))
        self.assertEqual(item["payType"], "Unpaid")
        self.assertFalse(item["isPaid"])
        self.assertEqual(LeaveRequest.objects.get(pk=historical.id).leave_type, "Historic Paid Leave")
        self.assertEqual(self.client.get("/api/v1/attendance-manager/leave-types").status_code, 404)
        self.assertEqual(self.client.post("/api/v1/attendance-manager/leave-types", {}, format="json").status_code, 404)
