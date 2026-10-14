from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.roles.models import Role
from apps.saas.models import FeatureGroup, PlanFeature, PlanFeatureAccess, SubscriptionPlan


class PlatformPlanBuilderTests(TestCase):
    def setUp(self):
        self.platform = User.objects.create_superuser(
            email="platform-plan-test@example.com", password="StrongTestPassword2026!"
        )
        self.group = FeatureGroup.objects.create(key="plan-builder-test", label="Plan Builder")
        self.feature = PlanFeature.objects.create(
            key="plan-builder-service", label="Service Job Cards", group=self.group
        )

    def test_platform_super_admin_sees_create_plan_link(self):
        self.client.force_login(self.platform)
        index = self.client.get(reverse("panel:plans"))
        self.assertEqual(index.status_code, 200)
        self.assertContains(index, reverse("panel:plan-create"))
        form = self.client.get(reverse("panel:plan-create"))
        self.assertEqual(form.status_code, 200)
        self.assertContains(form, "Create Plan")

    def test_create_plan_saves_pricing_and_enabled_features(self):
        self.client.force_login(self.platform)
        result = self.client.post(reverse("panel:plan-create"), {
            "key": "enterprise-qa",
            "name": "Enterprise QA",
            "description": "Testing platform plan create",
            "monthly_price": "3499.00",
            "yearly_price": "34990.00",
            "promo_percent": "10",
            "max_branches": "3",
            "max_users": "25",
            "included_storage_gb": "50",
            "extra_storage_rate": "15.00",
            "trial_days": "14",
            "sort_order": "4",
            "is_active": "on",
            "is_public": "on",
            "enabled_features": [str(self.feature.pk)],
        })
        self.assertRedirects(result, reverse("panel:plans"))
        plan = SubscriptionPlan.objects.get(key="enterprise-qa")
        self.assertEqual(str(plan.monthly_price), "3499.00")
        self.assertTrue(PlanFeatureAccess.objects.get(plan=plan, feature=self.feature).enabled)

    def test_workshop_owner_cannot_create_platform_plan(self):
        company = Company.objects.create(name="Private Garage", slug="private-garage-plan")
        branch = Branch.objects.create(company=company, name="Main", code="HO")
        role = Role.objects.create(company=company, name="Company Owner",
                                   code="SUPER_ADMIN", permissions=["*"])
        owner = User.objects.create_user(
            email="workshop-admin-plan@example.com", password="SomeStrongPassword2026!",
            company=company, branch=branch, role=role
        )
        self.client.force_login(owner)
        self.assertEqual(self.client.get(reverse("panel:plan-create")).status_code, 403)
