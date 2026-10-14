"""Plan Builder UI checks: catalog data, responsive forms, keys and entitlements."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Company
from apps.saas.models import (
    FeatureGroup, PlanFeature, PlanFeatureAccess, Subscription, SubscriptionPlan
)


class PlanBuilderUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email="plan-builder-ui@example.com", name="Plan Builder Admin",
            password="StrongPlanBuilder2026!"
        )
        cls.company = Company.objects.create(name="Test Garage", slug="plan-ui-test-garage")
        cls.group = FeatureGroup.objects.create(key="plan-ui-operations", label="Operations")
        cls.feature = PlanFeature.objects.create(
            key="plan-ui-jobs", label="Job Cards", group=cls.group
        )
        cls.active = SubscriptionPlan.objects.create(
            name="Starter", key="starter-ui",
            monthly_price=Decimal("599.00"), yearly_price=Decimal("5990.00"),
            is_public=True, is_active=True, max_users=3,
        )
        cls.archived = SubscriptionPlan.objects.create(
            name="Legacy Plan", key="legacy-ui",
            is_public=False, is_active=False,
        )
        PlanFeatureAccess.objects.create(plan=cls.active, feature=cls.feature, enabled=True)
        Subscription.objects.create(
            company=cls.company, plan="Starter", status="Active",
            plan_definition=cls.active, amount=Decimal("599.00")
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_plan_list_has_real_overview_and_feature_labels(self):
        result = self.client.get(reverse("panel:plans"))
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Subscription Plan Builder")
        self.assertContains(result, "Total Plans")
        self.assertContains(result, "Assigned Subscriptions")
        self.assertContains(result, "Job Cards")
        self.assertContains(result, "Checkout / Access")
        self.assertContains(result, "180 days access")
        self.assertContains(result, "plans-ui.css")
        self.assertContains(result, 'class="user-stat-value"', count=4)
        self.assertEqual(result.context["plan_total"], 2)
        self.assertEqual(result.context["plan_active"], 1)
        self.assertEqual(result.context["plan_public"], 1)
        self.assertEqual(result.context["subscriptions_assigned"], 1)
        self.assertIsNotNone(finders.find("control_panel/plans-ui.css"))

    def test_filters_are_combined_and_global_counts_remain_stable(self):
        url = reverse("panel:plans")
        result = self.client.get(url, {"q": "Starter", "status": "active", "visibility": "public"})
        self.assertEqual(len(result.context["plans"]), 1)
        self.assertContains(result, "Starter")
        self.assertNotContains(result, "Legacy Plan")
        self.assertEqual(result.context["plan_total"], 2)

        archived = self.client.get(url, {"status": "archived", "visibility": "private"})
        self.assertEqual(len(archived.context["plans"]), 1)
        self.assertContains(archived, "Legacy Plan")
        invalid = self.client.get(url, {"status": "unknown", "visibility": "invalid"})
        self.assertEqual(len(invalid.context["plans"]), 2)
        self.assertEqual(invalid.context["status"], "")
        self.assertEqual(invalid.context["visibility"], "")

    def test_create_plan_form_is_grouped_and_keeps_feature_checkboxes(self):
        response = self.client.get(reverse("panel:plan-create"))
        self.assertTemplateUsed(response, "control_panel/plan_editor.html")
        for label in (
            "Plan Identity", "Pricing &amp; Promotion", "Workspace Limits",
            "Feature Permissions", "Publishing Settings", "Create Plan",
        ):
            self.assertContains(response, label)
        self.assertContains(response, 'name="enabled_features"')
        self.assertContains(response, 'name="checkout_price"')
        self.assertContains(response, 'name="access_duration_days"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'data-plan-editor')
        self.assertContains(response, 'id="plan-key-preview"')
        self.assertNotContains(response, 'name="key"')
        self.assertContains(response, "Operations")
        self.assertContains(response, "Job Cards")

    def test_edit_plan_keeps_key_and_feature_selection_and_datetime(self):
        self.active.promo_ends_at = timezone.now() + timedelta(days=3)
        self.active.save(update_fields=["promo_ends_at"])
        response = self.client.get(reverse("panel:plan-edit", args=[self.active.pk]))
        self.assertTemplateUsed(response, "control_panel/plan_editor.html")
        self.assertContains(response, "data-fixed-key")
        self.assertContains(response, "starter-ui")
        self.assertContains(response, 'type="datetime-local"')
        self.assertContains(response, 'name="enabled_features"')
        self.assertContains(response, "Save Changes")

    def test_create_uses_server_generated_key_and_features(self):
        response = self.client.post(reverse("panel:plan-create"), {
            "name": "Business Plus", "key": "do-not-trust",
            "description": "All features for growing garages",
            "monthly_price": "1500.00", "yearly_price": "15000.00",
            "max_branches": "3", "max_users": "8",
            "included_storage_gb": "30", "extra_storage_rate": "10.00",
            "promo_percent": "5", "trial_days": "7", "sort_order": "3",
            "checkout_price": "4900.00", "access_duration_days": "210",
            "is_public": "on", "is_active": "on",
            "enabled_features": [str(self.feature.pk)],
        })
        self.assertRedirects(response, reverse("panel:plans"))
        plan = SubscriptionPlan.objects.get(key="business-plus")
        self.assertEqual(plan.monthly_price, Decimal("1500.00"))
        self.assertEqual(plan.checkout_price, Decimal("4900.00"))
        self.assertEqual(plan.access_duration_days, 210)
        self.assertTrue(PlanFeatureAccess.objects.get(plan=plan, feature=self.feature).enabled)

    def test_existing_subscription_amount_does_not_change_when_plan_price_edits(self):
        response = self.client.post(
            reverse("panel:plan-edit", args=[self.active.pk]), {
                "name": "Renamed Starter", "key": "attacker-supplied",
                "monthly_price": "799.00", "yearly_price": "7990.00",
                "max_branches": "1", "max_users": "3",
                "included_storage_gb": "5", "extra_storage_rate": "15.00",
                "promo_percent": "0", "trial_days": "14", "sort_order": "0",
                "checkout_price": "2999.00", "access_duration_days": "180",
                "is_public": "on", "is_active": "on",
                "enabled_features": [str(self.feature.pk)],
            }
        )
        self.assertRedirects(response, reverse("panel:plans"))
        self.active.refresh_from_db()
        self.assertEqual(self.active.key, "starter-ui")
        self.assertEqual(self.active.name, "Renamed Starter")
        sub = Subscription.objects.get(company=self.company)
        self.assertEqual(sub.amount, Decimal("599.00"))

    def test_anonymous_user_cannot_use_plan_builder(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("panel:plans")).status_code, 302)
        self.assertEqual(self.client.get(reverse("panel:plan-create")).status_code, 302)
