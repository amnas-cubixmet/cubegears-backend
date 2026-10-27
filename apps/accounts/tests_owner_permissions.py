"""Company owner privileges without platform/tenant boundary bypasses."""
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.companies.models import Company
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class CompanyOwnerPermissionTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Example Workshop", slug="test-owner-rbac", require_paid_features=False,
        )
        self.role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER_TEST",
            permissions=["dashboard.view"], is_active=True,
        )
        self.owner = User.objects.create_user(
            email="owner-rbac@example.test", password="safe-test-pass",
            name="Company Owner", company=self.company, role=self.role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.staff = User.objects.create_user(
            email="staff-rbac@example.test", password="safe-test-pass",
            name="Company Employee", company=self.company, role=self.role,
        )
        self.platform = User.objects.create_superuser(
            email="platform-rbac@example.test", password="safe-test-pass",
            name="Platform Administrator",
        )

    def test_owner_receives_role_permissions_on_own_company(self):
        self.assertTrue(user_has_permission(self.owner, "dashboard.view"))
        self.assertTrue(user_has_permission(self.owner, "settings.view"))
        self.assertTrue(user_has_permission(self.owner, "settings.manage"))
        self.assertTrue(user_has_permission(self.owner, "invoices.view"))
        self.assertTrue(user_has_permission(self.owner, "invoices.create"))
        self.assertTrue(user_has_permission(self.owner, "billing.edit"))

    def test_regular_staff_does_not_inherit_ownership(self):
        self.assertTrue(user_has_permission(self.staff, "dashboard.view"))
        self.assertFalse(user_has_permission(self.staff, "invoices.view"))
        self.assertFalse(user_has_permission(self.staff, "settings.manage"))
        self.assertFalse(user_has_permission(self.staff, "billing.edit"))

    def test_platform_superadmin_cannot_act_as_other_tenants(self):
        self.assertTrue(user_has_permission(self.platform, "panel.features.edit"))
        self.assertTrue(user_has_permission(self.platform, "panel.credits.edit"))
        self.assertFalse(user_has_permission(self.platform, "invoices.view"))
        self.assertFalse(user_has_permission(self.platform, "billing.edit"))

    def test_owner_must_still_have_active_company_and_role(self):
        self.role.is_active = False
        self.role.save(update_fields=["is_active"])
        self.assertFalse(user_has_permission(self.owner, "settings.manage"))
        self.assertFalse(user_has_permission(self.owner, "billing.edit"))

    def test_owner_cannot_bypass_paid_module_entitlement(self):
        self.company.require_paid_features = True
        self.company.save(update_fields=["require_paid_features"])
        self.assertTrue(user_has_permission(self.owner, "settings.manage"))
        self.assertFalse(user_has_permission(self.owner, "invoices.view"))
