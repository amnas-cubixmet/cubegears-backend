"""Platform admin remaining module UI regressions; no simulated payments or changes to auth."""
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.vehicles.models import Vehicle
from apps.roles.models import Role
from apps.saas.models import FeatureGroup, PlanFeature


class RemainingModulesUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email="remaining-ui-admin@example.com",
            name="Platform UI Admin", password="Secure-Platform-UI-2026!",
        )
        cls.company = Company.objects.create(name="Central Garage", slug="remaining-ui-central")
        cls.other_company = Company.objects.create(name="South Garage", slug="remaining-ui-south")
        cls.branch = Branch.objects.create(company=cls.company, name="Central Branch", code="CENTRAL", is_head_office=True)
        cls.inactive_branch = Branch.objects.create(company=cls.other_company, name="South Branch", code="SOUTH", is_active=False)
        cls.role = Role.objects.create(company=cls.company, name="Mechanic", code="MECHANIC", permissions=["jobs.view"])
        cls.customer = Customer.objects.create(
            company=cls.company, name="Alice", phone="9999991111",
            status="active", customer_type="business",
        )
        cls.archived_customer = Customer.objects.create(
            company=cls.other_company, name="Bob", phone="9999992222",
            status="archived", customer_type="individual",
        )
        cls.vehicle = Vehicle.objects.create(
            company=cls.company, customer=cls.customer, registration="KL10CD2026", status="Active",
        )
        cls.group = FeatureGroup.objects.create(key="ui-module-group", label="Module Group")
        cls.feature = PlanFeature.objects.create(key="ui-feature", label="UI Feature", group=cls.group, is_active=True)

    def setUp(self):
        self.client.force_login(self.admin)

    def test_customer_directory_real_counts_and_combined_filters(self):
        result = self.client.get(reverse("panel:customers"), {
            "q": "Alice", "company": str(self.company.pk),
            "type": "business", "status": "active",
        })
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Customer Management")
        self.assertContains(result, "Alice")
        self.assertNotContains(result, ">Bob<")
        self.assertEqual(result.context["rows"].paginator.count, 1)
        self.assertEqual(result.context["total_customers"], 2)
        self.assertEqual(result.context["active_customers"], 1)
        self.assertEqual(result.context["business_customers"], 1)
        self.assertEqual(result.context["customer_companies"], 2)
        self.assertContains(result, "platform-modules-ui.css")

        form = self.client.get(reverse("panel:customer-create"))
        self.assertTemplateUsed(form, "control_panel/customer_form.html")
        for field in ("company", "branch", "name", "phone", "whatsapp", "email",
                      "customer_type", "company_name", "gstin", "address", "city", "state",
                      "pincode", "notes", "credit_limit", "status", "last_service_date", "next_reminder_at"):
            self.assertContains(form, f'name="{field}"')
        self.assertContains(form, 'name="csrfmiddlewaretoken"')

    def test_vehicle_directory_and_form_preserve_all_fields(self):
        response = self.client.get(reverse("panel:vehicles"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vehicle Management")
        self.assertContains(response, "KL10CD2026")
        self.assertEqual(response.context["total_records"], 1)
        self.assertEqual(response.context["active_records"], 1)
        self.assertEqual(response.context["companies_count"], 1)
        add = self.client.get(reverse("panel:vehicle-create"))
        self.assertTemplateUsed(add, "control_panel/remaining_form.html")
        for field in ("company", "branch", "customer", "registration", "make", "model",
                      "variant", "year", "fuel_type", "transmission", "color", "odometer",
                      "vin", "engine_no", "last_service_date", "next_service_due",
                      "insurance_expiry", "notes", "status"):
            self.assertContains(add, f'name="{field}"')

    def test_branches_directory_counts_and_status_filter(self):
        result = self.client.get(reverse("panel:branches"), {"status": "inactive"})
        self.assertEqual(result.context["rows"].paginator.count, 1)
        self.assertContains(result, "South Branch")
        self.assertEqual(result.context["total_records"], 2)
        self.assertEqual(result.context["active_records"], 1)
        self.assertEqual(result.context["special_records"], 1)
        edit = self.client.get(reverse("panel:branch-edit", args=[self.branch.pk]))
        self.assertTemplateUsed(edit, "control_panel/remaining_form.html")
        self.assertContains(edit, "Save Changes")
        self.assertContains(edit, 'name="is_head_office"')

    def test_roles_list_and_permission_editor_keep_rbac_controls(self):
        result = self.client.get(reverse("panel:roles"))
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Roles &amp; Permissions")
        self.assertContains(result, "Mechanic")
        self.assertEqual(result.context["total_records"], 1)
        form = self.client.get(reverse("panel:role-edit", args=[self.role.pk]))
        self.assertTemplateUsed(form, "control_panel/remaining_form.html")
        self.assertContains(form, 'name="permissions"')
        self.assertContains(form, "full-access (*) permission")
        self.assertContains(form, 'name="csrfmiddlewaretoken"')

    def test_security_and_storage_are_read_only_views(self):
        security = self.client.get(reverse("panel:security"))
        self.assertEqual(security.status_code, 200)
        self.assertContains(security, "Security &amp; Activity")
        self.assertContains(security, "Read-only event history")
        self.assertEqual(security.context["total_events"], 0)
        storage = self.client.get(reverse("panel:storage"))
        self.assertEqual(storage.status_code, 200)
        self.assertContains(storage, "Storage Management")
        self.assertEqual(storage.context["snapshot_count"], 0)
        self.assertEqual(storage.context["covered_companies"], 0)

    def test_feature_catalog_only_shows_paid_labels_and_preserves_edit_routes(self):
        list_response = self.client.get(reverse("panel:features"))
        self.assertContains(list_response, "Paid Feature Labels")
        self.assertContains(list_response, "Multi-feature packages")
        self.assertContains(list_response, "Create Paid Label")
        self.assertNotContains(list_response, "Discovered Module Inventory")
        self.assertNotContains(list_response, "Sync Missing Modules")
        self.assertNotContains(list_response, "Discovered Workshop Modules")
        self.assertNotContains(list_response, "Module Groups")
        self.assertNotContains(list_response, "UI Feature")
        self.assertNotIn("groups", list_response.context)
        self.assertNotIn("discovered_modules", list_response.context)
        self.assertEqual(PlanFeature.objects.count(), 1)
        self.assertEqual(FeatureGroup.objects.count(), 1)
        self.assertTemplateUsed(
            self.client.get(reverse("panel:feature-create")),
            "control_panel/remaining_form.html",
        )
        self.assertTemplateUsed(
            self.client.get(reverse("panel:featuregroup-create")),
            "control_panel/remaining_form.html",
        )

    def test_credits_are_existing_wallet_dashboard_not_dummy_plans(self):
        response = self.client.get(reverse("panel:credits"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Credit Wallet Control")
        self.assertContains(response, "Verified purchases")
        self.assertContains(response, "Full credit activity ledger")
        self.assertContains(response, 'value="adjustment_request_id"', count=0)
        self.assertEqual(response.context["package_total"], 0)
        self.assertEqual(response.context["wallet_total"], 0)
        self.assertContains(response, 'name="request_id"')
        self.assertContains(response, 'name="action"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_credit_controls_are_split_into_six_functional_accessible_tabs(self):
        response = self.client.get(reverse("panel:credits"))
        self.assertEqual(response.status_code, 200)
        for key in ("packages", "pricing", "adjustment", "wallets", "purchases", "ledger"):
            self.assertContains(response, f'data-credit-tab="{key}"', count=1)
            self.assertContains(response, f'data-credit-panel="{key}"', count=1)
            self.assertContains(response, f'aria-controls="credit-panel-{key}"', count=1)
            self.assertContains(response, f'aria-labelledby="credit-tab-{key}"', count=1)
        self.assertContains(response, 'role="tab"', count=6)
        self.assertContains(response, 'role="tabpanel"', count=6)
        self.assertContains(response, 'data-credit-tab-navigation')
        self.assertContains(response, "credits-tabs.js")
        self.assertIsNotNone(finders.find("control_panel/credits-tabs.js"))

        # Keep the server-validated forms, audited adjustment ID, CSRF.
        for action in ("package", "price", "adjust"):
            self.assertContains(response, f'name="action" value="{action}"', count=1)
        # Three credit action forms plus the shared sidebar logout form.
        self.assertContains(response, 'name="csrfmiddlewaretoken"', count=5)  # Includes separate storage pricing form
        self.assertContains(response, 'name="request_id"', count=1)
        self.assertContains(response, 'name="reason"')
        self.assertContains(response, 'name="delta"')
        self.assertContains(response, "All companies")
        self.assertContains(response, 'name="company"')

    def test_credit_ledger_company_filter_preserved_in_tabbed_view(self):
        response = self.client.get(
            reverse("panel:credits"), {"company": str(self.company.pk)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Full credit activity ledger")
        self.assertContains(response, 'data-credit-panel="ledger"')
        self.assertEqual(response.context["filter_company"], str(self.company.pk))

    def test_anonymous_access_still_requires_login(self):
        self.client.logout()
        for page in ("panel:customers", "panel:vehicles", "panel:branches", "panel:roles",
                     "panel:security", "panel:storage", "panel:features", "panel:credits"):
            self.assertEqual(self.client.get(reverse(page)).status_code, 302)

    def test_shared_static_styles_are_registered(self):
        self.assertIsNotNone(finders.find("control_panel/platform-modules-ui.css"))
