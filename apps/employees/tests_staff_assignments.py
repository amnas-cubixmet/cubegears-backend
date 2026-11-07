"""Atomic branch-safe staff assignment integration checks for Part 06."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.employees.models import Employee
from apps.roles.models import Role


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False, CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class StaffAssignmentsTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Staff Garage", slug="part06-staff-garage", require_paid_features=False)
        self.a_branch = Branch.objects.create(company=self.company, name="A", code="A")
        self.b_branch = Branch.objects.create(company=self.company, name="B", code="B")
        role = Role.objects.create(company=self.company, name="Owner", code="OWNER_PART06", permissions=["*"], is_system=True)
        owner = User.objects.create_user(email="part06-owner@example.test", password="StrongPass123!", name="Owner", company=self.company, branch=self.a_branch, role=role)
        self.company.owner = owner
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(owner)
        self.first = Employee.objects.create(company=self.company, branch=self.a_branch, name="First", employee_code="EMP-001", status="Active")
        self.second = Employee.objects.create(company=self.company, branch=self.a_branch, name="Second", employee_code="EMP-002", status="Active")
        self.remote = Employee.objects.create(company=self.company, branch=self.b_branch, name="Remote", employee_code="EMP-003", status="Active")
        other = Company.objects.create(name="Outside", slug="part06-outside", require_paid_features=False)
        self.outside = Employee.objects.create(company=other, name="Outsider", employee_code="EMP-004", status="Active")

    def test_shift_assignment_validates_every_id_before_changing_data(self):
        shift = self.client.post("/api/v1/employees/shifts", {
            "name": "Day Shift", "start_time": "09:00:00", "end_time": "18:00:00",
            "branchId": str(self.a_branch.pk),
        }, format="json")
        self.assertEqual(shift.status_code, 201, shift.data)
        url = f"/api/v1/employees/shifts/{shift.data['id']}/assign"
        good = self.client.post(url, {"staffIds": [str(self.first.pk), str(self.second.pk)]}, format="json")
        self.assertEqual(good.status_code, 200, good.data)
        for unwanted in (self.remote, self.outside):
            bad = self.client.post(url, {"staffIds": [str(self.first.pk), str(unwanted.pk)]}, format="json")
            self.assertEqual(bad.status_code, 400, bad.data)
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {"staffIds": [str(self.first.pk), str(self.first.pk)]}, format="json").status_code, 400)
        self.second.refresh_from_db()
        self.assertEqual(str(self.second.shift_id), str(shift.data['id']))

    def test_team_assignment_is_atomic_and_branch_scoped(self):
        team = self.client.post("/api/v1/employees/teams", {
            "name": "Engine Team", "branchId": str(self.a_branch.pk),
        }, format="json")
        self.assertEqual(team.status_code, 201, team.data)
        url = f"/api/v1/employees/teams/{team.data['id']}/assign"
        bad = self.client.post(url, {"staffIds": [str(self.first.pk), str(self.remote.pk)]}, format="json")
        self.assertEqual(bad.status_code, 400, bad.data)
        self.first.refresh_from_db()
        self.assertIsNone(self.first.team_id)
        good = self.client.post(url, {"staffIds": [str(self.first.pk), str(self.second.pk)]}, format="json")
        self.assertEqual(good.status_code, 200, good.data)
        self.first.refresh_from_db()
        self.assertEqual(self.first.department_name, "Engine Team")

    def test_staff_view_permission_cannot_assign_shift(self):
        viewer_role = Role.objects.create(company=self.company, name="Viewer", code="VIEWER_PART06", permissions=["staff.view"])
        viewer = User.objects.create_user(email="part06-viewer@example.test", name="Viewer", password="StrongPass123!", company=self.company, role=viewer_role, branch=self.a_branch)
        self.client.force_authenticate(viewer)
        bad = self.client.post("/api/v1/employees/shifts/00000000-0000-0000-0000-000000000001/assign", {"staffIds": [str(self.first.pk)]}, format="json")
        self.assertEqual(bad.status_code, 403)
