"""Permission isolation regressions for payment, wage and attendance actions."""
import importlib
from uuid import uuid4

from django.apps import apps
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class GranularFinancialPermissionTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="RBAC Audit Workshop", slug="rbac-audit-workshop",
            require_paid_features=False,
        )
        owner_role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER_RBAC",
            permissions=["*"], is_system=True,
        )
        self.owner = User.objects.create_user(
            email="owner-permission-audit@example.test",
            password="audit-test-password", name="Owner",
            company=self.company, role=owner_role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.role = Role.objects.create(
            company=self.company, name="Wage Viewer", code="WAGE_VIEWER",
            permissions=["payroll.view", "attendance.team.view"],
        )
        self.staff = User.objects.create_user(
            email="staff-permission-audit@example.test",
            password="audit-test-password", name="Wage Viewer",
            company=self.company, role=self.role,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.staff)
        self.employee = uuid4()

    def grant(self, *codes):
        self.role.permissions = list(dict.fromkeys([*self.role.permissions, *codes]))
        self.role.save(update_fields=["permissions"])
        # The APIClient uses this authenticated instance across requests.
        self.staff.role = self.role

    def test_wage_viewer_cannot_change_rates_pay_or_reverse(self):
        endpoints = (
            f"/api/v1/payroll/daily-wages/employees/{self.employee}/rates",
            f"/api/v1/payroll/daily-wages/employees/{self.employee}/finalize",
            f"/api/v1/payroll/daily-wages/employees/{self.employee}/extras",
            f"/api/v1/payroll/daily-wages/employees/{self.employee}/adjustments",
            f"/api/v1/payroll/daily-wages/employees/{self.employee}/pay",
            f"/api/v1/payroll/daily-wages/payments/{uuid4()}/reverse",
        )
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.post(endpoint, {}, format="json").status_code, 403)

    def test_wage_rate_grant_never_grants_payment_or_reversal(self):
        self.grant("payroll.rates.manage")
        rate = self.client.post(
            f"/api/v1/payroll/daily-wages/employees/{self.employee}/rates",
            {}, format="json",
        )
        self.assertNotEqual(rate.status_code, 403)
        self.assertEqual(
            self.client.post(f"/api/v1/payroll/daily-wages/employees/{self.employee}/pay", {}, format="json").status_code,
            403,
        )
        self.assertEqual(
            self.client.post(f"/api/v1/payroll/daily-wages/payments/{uuid4()}/reverse", {}, format="json").status_code,
            403,
        )

    def test_legacy_payroll_edit_on_new_role_does_not_grant_payments(self):
        self.grant("payroll.edit")
        self.assertEqual(
            self.client.post(f"/api/v1/payroll/daily-wages/employees/{self.employee}/pay", {}, format="json").status_code,
            403,
        )

    def test_payment_history_is_independently_authorized(self):
        endpoint = "/api/v1/payroll/daily-wages/payments"
        self.assertEqual(self.client.get(endpoint).status_code, 403)
        self.grant("payroll.payments.view")
        self.assertEqual(self.client.get(endpoint).status_code, 200)

    def test_overtime_approval_is_separate_from_view(self):
        url = f"/api/v1/attendance/overtime/{uuid4()}/approve"
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 403)
        self.grant("attendance.overtime.approve")
        self.assertNotEqual(self.client.post(url, {}, format="json").status_code, 403)

    def test_supplier_bills_and_payments_need_both_domains(self):
        bills = "/api/v1/inventory/procurement/bills"
        payments = "/api/v1/inventory/procurement/payments"
        self.grant("stock.view")
        self.assertEqual(self.client.get(bills).status_code, 403)
        self.grant("payments.view")
        self.assertEqual(self.client.get(bills).status_code, 200)
        self.assertEqual(self.client.get(payments).status_code, 200)
        self.assertEqual(self.client.post(payments, {}, format="json").status_code, 403)

    def test_role_upgrade_is_additive_and_idempotent(self):
        self.role.permissions = ["payroll.view", "payroll.edit", "attendance.manage"]
        self.role.save(update_fields=["permissions"])
        migrate = importlib.import_module(
            "apps.roles.migrations.0002_granular_attendance_payroll_permissions"
        ).preserve_existing_roles
        migrate(apps, None)
        self.role.refresh_from_db()
        self.assertIn("payroll.payments.record", self.role.permissions)
        self.assertIn("payroll.payments.reverse", self.role.permissions)
        self.assertIn("attendance.overtime.approve", self.role.permissions)
        original = self.role.permissions[:]
        migrate(apps, None)
        self.role.refresh_from_db()
        self.assertEqual(self.role.permissions, original)
