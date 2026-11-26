"""First-login setup and connected workflow-package security regressions."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.credits.models import CreditWallet, CompanyBundleAccess, CompanyPostpaidFeature, FeatureBundle
from apps.employees.models import Employee
from apps.roles.models import Role
from apps.saas.feature_discovery import module_inventory
from apps.saas.module_groups import (
    WORKFLOW_PACKAGES, ALL_GROUPED_MODULES, billed_feature_keys,
    expand_entitlements, legacy_individual_feature_bundle,
)
from apps.saas.models import CompanySetting, PlanFeature


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
                   CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class FirstLoginMandatorySetupTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="New Workshop", slug="new-workshop", require_paid_features=False,
        )
        self.branch = Branch.objects.create(
            company=self.company, name="Head Office", code="HO", is_head_office=True,
        )
        role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER",
            permissions=["*"], is_active=True,
        )
        self.owner = User.objects.create_user(
            email="owner-onboarding22@example.test", password="OwnerPass123!",
            name="Workshop Owner", company=self.company,
            branch=self.branch, role=role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        CreditWallet.objects.get_or_create(company=self.company)
        CompanySetting.objects.create(
            company=self.company, branch=self.branch, category="onboarding",
            data={"required": True, "completed": False, "completedSteps": []},
        )

    @staticmethod
    def company_fields(**overrides):
        return {
            "name": "GPT Workshop", "legal_name": "GPT Workshop Trading",
            "phone": "+919999000123", "email": "",
            "address": "Workshop Road, Main Junction",
            "city": "Kottayam", "state": "Kerala", "country": "India",
            "pincode": "686001", "gstin": "", "gstNotRegistered": True,
            **overrides,
        }

    @staticmethod
    def branch_fields(**overrides):
        return {
            "name": "Main Garage", "phone": "+919999000123",
            "address": "Workshop Road, Main Junction", "city": "Kottayam",
            "state": "Kerala", "pincode": "686001", **overrides,
        }

    def step(self, name, payload):
        return self.client.patch(
            "/api/v1/company/onboarding",
            {"step": name, "data": payload}, format="json",
        )

    def test_owner_is_isolated_to_onboarding_and_wallet_balance_is_real(self):
        wallet = CreditWallet.objects.get(company=self.company)
        wallet.balance = Decimal("215.75")
        wallet.save(update_fields=["balance"])
        state = self.client.get("/api/v1/company/onboarding")
        self.assertEqual(state.status_code, 200, state.data)
        self.assertTrue(state.data["required"])
        self.assertEqual(state.data["account"]["walletBalanceCredits"], "215.75")
        self.assertEqual(state.data["account"]["companyName"], "New Workshop")
        self.assertFalse(user_has_permission(self.owner, "jobs.view"))
        self.assertFalse(user_has_permission(self.owner, "payroll.view"))
        self.assertTrue(user_has_permission(self.owner, "company.manage"))
        # No operation of the free account flow can create credits.
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("215.75"))

    def test_required_fields_and_explicit_gst_opt_out(self):
        missing = self.step("company", self.company_fields(address=""))
        self.assertEqual(missing.status_code, 400, missing.data)
        self.assertIn("address", missing.data)
        no_choice = self.step("company", self.company_fields(gstNotRegistered=False))
        self.assertEqual(no_choice.status_code, 400, no_choice.data)
        self.assertIn("gstin", no_choice.data)
        conflict = self.step("company", self.company_fields(
            gstNotRegistered=True, gstin="32ABCDE1234F1Z5",
        ))
        self.assertEqual(conflict.status_code, 400, conflict.data)
        invalid = self.step("company", self.company_fields(
            gstNotRegistered=False, gstin="123",
        ))
        self.assertEqual(invalid.status_code, 400, invalid.data)
        self.assertEqual(self.step("company", self.company_fields()).status_code, 200)
        self.assertEqual(self.company.gstin, "")
        self.assertTrue(self.client.get(
            "/api/v1/company/onboarding"
        ).data["company"]["gstNotRegistered"])
        bad_branch = self.step("branch", self.branch_fields(phone=""))
        self.assertEqual(bad_branch.status_code, 400)
        self.assertIn("phone", bad_branch.data)

    def test_registered_gst_requires_real_format_and_remains_saved(self):
        registered = self.step("company", self.company_fields(
            gstNotRegistered=False, gstin="32ABCDE1234F1Z5",
        ))
        self.assertEqual(registered.status_code, 200, registered.data)
        self.company.refresh_from_db()
        self.assertEqual(self.company.gstin, "32ABCDE1234F1Z5")
        self.assertFalse(registered.data["company"]["gstNotRegistered"])

    def test_no_dashboard_or_paid_route_until_all_steps_complete(self):
        self.assertEqual(self.step("company", self.company_fields()).status_code, 200)
        self.assertEqual(self.step("branch", self.branch_fields()).status_code, 200)
        self.assertEqual(self.step("operations", {
            "workingStart": "09:00", "workingEnd": "18:30",
            "bookingSlotMinutes": 30, "jobPrefix": "GPT-",
        }).status_code, 200)
        unfinished = self.client.post("/api/v1/company/onboarding/complete")
        self.assertEqual(unfinished.status_code, 400)
        self.assertEqual(unfinished.data["missingSteps"], ["bank", "billing"])
        self.assertEqual(self.step("bank", {"mode": "skip"}).status_code, 200)
        self.assertFalse(user_has_permission(self.owner, "jobs.view"))
        self.assertEqual(self.step("billing", {
            "invoicePrefix": "INV-", "paymentTermsDays": 5,
            "defaultTaxRate": "18.00", "rounding": True,
        }).status_code, 200)
        done = self.client.post("/api/v1/company/onboarding/complete")
        self.assertEqual(done.status_code, 200, done.data)
        self.assertTrue(done.data["completed"])
        self.assertTrue(user_has_permission(self.owner, "jobs.view"))
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("0.00"))

    def test_stale_completed_steps_are_recoverable_without_resetting_other_data(self):
        self.assertEqual(self.step("company", self.company_fields()).status_code, 200)
        self.assertEqual(self.step("branch", self.branch_fields()).status_code, 200)
        self.assertEqual(self.step("operations", {
            "workingStart": "09:00", "workingEnd": "18:30",
            "bookingSlotMinutes": 30, "jobPrefix": "GPT-",
        }).status_code, 200)
        self.assertEqual(self.step("bank", {"mode": "skip"}).status_code, 200)
        self.assertEqual(self.step("billing", {
            "invoicePrefix": "INV-", "paymentTermsDays": 5,
            "defaultTaxRate": "18.00", "rounding": True,
        }).status_code, 200)
        # Simulate data saved under earlier onboarding rules or edited later.
        Branch.objects.filter(pk=self.branch.pk).update(phone="")
        url = "/api/v1/company/onboarding"
        snapshot = self.client.get(url)
        self.assertEqual(snapshot.status_code, 200)
        self.assertNotIn("branch", snapshot.data["completedSteps"])
        self.assertIn("phone", snapshot.data["missingFields"]["branch"])
        self.assertIn("bank", snapshot.data["completedSteps"])
        self.assertIn("billing", snapshot.data["completedSteps"])
        rejected = self.client.post(url + "/complete")
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("phone", rejected.data["missingFields"]["branch"])
        repaired = self.step("branch", self.branch_fields())
        self.assertEqual(repaired.status_code, 200)
        self.assertEqual(repaired.data["missingFields"], {})
        finished = self.client.post(url + "/complete")
        self.assertEqual(finished.status_code, 200)
        self.assertTrue(finished.data["completed"])

    def test_legacy_tenant_without_required_setup_is_not_locked(self):
        CompanySetting.objects.filter(company=self.company, category="onboarding").delete()
        self.assertTrue(user_has_permission(self.owner, "jobs.view"))
        state = self.client.get("/api/v1/company/onboarding")
        self.assertFalse(state.data["required"])

    def test_staff_cannot_complete_owner_setup_or_bypass_operational_permissions(self):
        staff_role = Role.objects.create(
            company=self.company, name="Staff", code="STAFF",
            permissions=["jobs.view", "payroll.view"], is_active=True,
        )
        staff = User.objects.create_user(
            email="worker-onboarding22@example.test", password="WorkerPass123!",
            name="Worker", company=self.company, role=staff_role,
        )
        self.assertFalse(user_has_permission(staff, "jobs.view"))
        self.assertFalse(user_has_permission(staff, "payroll.view"))
        self.client.force_authenticate(staff)
        self.assertEqual(self.client.post(
            "/api/v1/company/onboarding/complete"
        ).status_code, 403)


class WorkflowFeaturePackagesTests(TestCase):
    def test_all_implemented_billable_modules_are_covered_once(self):
        inventory = module_inventory()
        keys = {item["key"] for item in inventory if item["billable"]}
        self.assertTrue({"workshop_operations", "accounts_tax",
                         "attendance_payroll", "storage"} <= keys)
        self.assertNotIn("business_insights", keys)
        self.assertIn("reports", WORKFLOW_PACKAGES["accounts_tax"]["modules"])
        self.assertFalse(ALL_GROUPED_MODULES & keys)
        members = [
            member for package in WORKFLOW_PACKAGES.values() for member in package["modules"]
        ]
        self.assertEqual(len(members), len(set(members)))
        self.assertEqual(set(members), ALL_GROUPED_MODULES)
        self.assertIn("finance", ALL_GROUPED_MODULES)
        self.assertNotIn("staff", ALL_GROUPED_MODULES)

    def test_entitlement_expands_without_merging_role_codes(self):
        self.assertEqual(billed_feature_keys("payroll"), ("payroll", "attendance_payroll"))
        self.assertEqual(billed_feature_keys("jobs"), ("jobs", "workshop_operations"))
        expanded = expand_entitlements({"attendance_payroll"})
        self.assertIn("attendance", expanded)
        self.assertIn("payroll", expanded)
        self.assertNotIn("jobs", expanded)
        self.assertTrue(legacy_individual_feature_bundle({"attendance"}))
        self.assertTrue(legacy_individual_feature_bundle({"business_insights"}))
        self.assertFalse(legacy_individual_feature_bundle({"attendance_payroll"}))
        self.assertEqual(billed_feature_keys("reports"), ("reports", "accounts_tax", "business_insights"))
        self.assertIn("reports", expand_entitlements({"accounts_tax"}))
        self.assertIn("reports", expand_entitlements({"business_insights"}))
        self.assertNotIn("finance", expand_entitlements({"business_insights"}))

    @override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=True, CREDIT_LABEL_ONLY_ACCESS_ENABLED=True,
                       CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=True)
    def test_reports_are_sold_with_accounts_and_existing_access_stays_valid(self):
        company = Company.objects.create(name="Reports Garage", slug="reports-garage", require_paid_features=True)
        owner_role = Role.objects.create(company=company, name="Owner", code="OWNER", permissions=["*"])
        owner = User.objects.create(email="reports-owner@example.test", company=company, role=owner_role)
        company.owner = owner
        company.save(update_fields=["owner"])
        reports_role = Role.objects.create(company=company, name="Reporter", code="REPORTER", permissions=["reports.view"])
        reporter = User.objects.create(email="reporter@example.test", company=company, role=reports_role)
        restricted_role = Role.objects.create(company=company, name="Worker", code="WORKER", permissions=[])
        restricted = User.objects.create(email="report-worker@example.test", company=company, role=restricted_role)
        accounts = FeatureBundle.objects.get(key="workflow-accounts_tax")
        legacy = FeatureBundle.objects.get(key="workflow-business_insights")
        client = APIClient()
        client.force_authenticate(owner)
        catalog_url = "/api/v1/saas/credits/bundles"
        purchase_url = f"{catalog_url}/purchase"

        catalog = client.get(catalog_url)
        self.assertEqual(catalog.status_code, 200, catalog.data)
        by_id = {row["id"]: row for row in catalog.data}
        self.assertNotIn(legacy.pk, by_id)
        self.assertIn("reports", by_id[accounts.pk]["includedModuleKeys"])
        self.assertIn("Reports", by_id[accounts.pk]["includedModules"])
        self.assertFalse(user_has_permission(reporter, "reports.view"))
        rejected = client.post(purchase_url, {"bundleId": legacy.pk}, format="json")
        self.assertEqual(rejected.status_code, 409, rejected.data)
        self.assertEqual(rejected.data["code"], "REPORTS_IN_ACCOUNTS_TAX")

        access = CompanyBundleAccess.objects.create(
            company=company, bundle=legacy, enabled=True,
            valid_until=timezone.now() + timedelta(days=5), billing_type_at_purchase="monthly",
        )
        self.assertTrue(user_has_permission(reporter, "reports.view"))
        self.assertFalse(user_has_permission(restricted, "reports.view"))
        legacy_row = next(row for row in client.get(catalog_url).data if row["id"] == legacy.pk)
        self.assertTrue(legacy_row["active"])
        self.assertFalse(legacy_row["purchaseEnabled"])
        overlap = client.post(purchase_url, {"bundleId": accounts.pk}, format="json")
        self.assertEqual(overlap.status_code, 409, overlap.data)
        self.assertEqual(overlap.data["code"], "EXISTING_LEGACY_ENTITLEMENT")

        access.delete()
        price_label = legacy.module_price
        price_label.monthly_postpaid = True
        price_label.save(update_fields=["monthly_postpaid"])
        postpaid = CompanyPostpaidFeature.objects.create(company=company, feature=price_label.feature, enabled=True)
        self.assertTrue(user_has_permission(reporter, "reports.view"))
        legacy_row = next(row for row in client.get(catalog_url).data if row["id"] == legacy.pk)
        self.assertTrue(legacy_row["active"])
        self.assertFalse(legacy_row["purchaseEnabled"])
        postpaid.enabled = False
        postpaid.save(update_fields=["enabled"])
        CompanyBundleAccess.objects.create(
            company=company, bundle=accounts, enabled=True,
            valid_until=timezone.now() + timedelta(days=5), billing_type_at_purchase="monthly",
        )
        self.assertTrue(user_has_permission(reporter, "reports.view"))
        self.assertFalse(user_has_permission(restricted, "reports.view"))

    @override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=True,
                       CREDIT_LABEL_ONLY_ACCESS_ENABLED=True)
    def test_one_paid_workforce_feature_unlocks_both_modules_but_not_unrelated_roles(self):
        company = Company.objects.create(
            name="Feature Test Garage", slug="package-feature-garage",
            require_paid_features=True,
        )
        role = Role.objects.create(
            company=company, name="Owner", code="OWNER_TEST",
            permissions=["*"],
        )
        owner = User.objects.create_user(
            email="owner-pkg-test@example.test", password="TestPass123!",
            name="Owner", company=company, role=role,
        )
        company.owner = owner
        company.save(update_fields=["owner"])
        bundle = FeatureBundle.objects.get(key="workflow-attendance_payroll")
        CompanyBundleAccess.objects.create(
            company=company, bundle=bundle, enabled=True,
            valid_until=timezone.now() + timedelta(days=20),
            billing_type_at_purchase="monthly",
        )
        self.assertTrue(user_has_permission(owner, "attendance.manage"))
        self.assertTrue(user_has_permission(owner, "payroll.view"))
        self.assertFalse(user_has_permission(owner, "jobs.view"))
        staff_role = Role.objects.create(
            company=company, name="Punch Only", code="PUNCH_ONLY",
            permissions=["attendance.self"],
        )
        staff = User.objects.create_user(
            email="staff-pkg-test@example.test", password="TestPass123!",
            name="Employee", company=company, role=staff_role,
        )
        Employee.objects.create(company=company, user=staff, name="Employee", employee_code="PKG-01", status="Active")
        self.assertTrue(user_has_permission(staff, "attendance.self"))
        self.assertFalse(user_has_permission(staff, "payroll.view"))

    def test_group_products_exist_and_are_not_billed_per_member(self):
        for group_key in WORKFLOW_PACKAGES:
            bundle = FeatureBundle.objects.get(key="workflow-" + group_key)
            self.assertEqual(bundle.features.count(), 1)
            self.assertEqual(bundle.features.first().key, group_key)
            self.assertEqual(bundle.per_user_credits, Decimal("5.00"))
        self.assertTrue(PlanFeature.objects.filter(key="storage").exists())
