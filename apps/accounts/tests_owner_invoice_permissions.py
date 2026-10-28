"""Owner permissions must survive stale role catalogs without skipping billing."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.accounts.permissions import user_has_permission
from apps.invoices.views import InvoiceViewSet


def user_fixture(*, owner=True, permissions=None):
    company = SimpleNamespace(
        id="workshop-1",
        is_active=True,
        owner_id="user-1" if owner else "other-owner",
    )
    role = SimpleNamespace(
        id="role-1",
        is_active=True,
        company_id="workshop-1",
        permissions=[] if permissions is None else permissions,
    )
    return SimpleNamespace(
        pk="user-1", company=company, company_id=company.id,
        is_authenticated=True, is_active=True, role=role, role_id=role.id,
    )


class CompanyOwnerInvoicePermissionsTests(SimpleTestCase):
    @patch("apps.saas.access.feature_allowed", return_value=True)
    @patch("apps.credits.cycle_billing.company_is_overdue", return_value=False)
    @patch("apps.credits.monthly_policy.billing_status", return_value={"blocked": False})
    def test_verified_owner_has_invoice_role_authority_even_if_role_catalog_empty(
        self, _billing, _overdue, _entitlement,
    ):
        self.assertTrue(user_has_permission(user_fixture(), "invoices.view"))
        self.assertTrue(user_has_permission(user_fixture(), "invoices.edit"))
        self.assertTrue(user_has_permission(user_fixture(), "company.manage"))

    @patch("apps.saas.access.feature_allowed", return_value=False)
    @patch("apps.credits.cycle_billing.company_is_overdue", return_value=False)
    @patch("apps.credits.monthly_policy.billing_status", return_value={"blocked": False})
    def test_owner_cannot_bypass_unpurchased_invoice_entitlement(
        self, _billing, _overdue, _entitlement,
    ):
        self.assertFalse(user_has_permission(user_fixture(), "invoices.view"))

    @patch("apps.credits.monthly_policy.billing_status", return_value={"blocked": False})
    def test_nonowner_with_empty_role_cannot_view_invoices(self, _billing):
        self.assertFalse(user_has_permission(user_fixture(owner=False), "invoices.view"))

    def test_finalizing_invoice_does_not_automatically_require_stock_permission(self):
        self.assertEqual(InvoiceViewSet.action_permission_map["finalize"], "invoices.edit")
