"""Subscription listing, detail and editor UI regressions with real tenant billing data."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Company
from apps.saas.models import Subscription, SubscriptionPlan


class SubscriptionManagementUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email="subscription-style-admin@example.com", name="Subscription Admin",
            password="Subscription-secure-password-2026!",
        )
        cls.alpha = Company.objects.create(name="Alpha Garage", slug="billing-alpha")
        cls.beta = Company.objects.create(name="Beta Garage", slug="billing-beta")
        cls.active_monthly = Subscription.objects.create(
            company=cls.alpha, plan="Starter", status="Active", billing_cycle="monthly",
            amount=Decimal("499.00"), currency="INR", seats=3,
            starts_at=timezone.now() - timedelta(days=10),
            renews_at=timezone.now() + timedelta(days=10),
        )
        cls.active_yearly = Subscription.objects.create(
            company=cls.beta, plan="Pro", status="active", billing_cycle="yearly",
            amount=Decimal("9999.00"), currency="INR", seats=12,
            renews_at=timezone.now() + timedelta(days=80),
        )
        cls.paused = Subscription.objects.create(
            company=cls.alpha, plan="Standard", status="Paused", billing_cycle="monthly",
            amount=Decimal("1599.00"), currency="INR", seats=8,
            renews_at=timezone.now() + timedelta(days=5),
        )
        cls.cancelled = Subscription.objects.create(
            company=cls.beta, plan="Legacy", status="Cancelled", billing_cycle="annual",
            amount=Decimal("2400.00"), currency="USD", seats=2,
        )
        cls.defined_plan = SubscriptionPlan.objects.create(
            key="subscription-test-pro", name="Test Pro",
            monthly_price=Decimal("800.00"), yearly_price=Decimal("8000.00"),
            max_users=14,
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_subscription_list_shows_global_counts_and_responsive_fields(self):
        response = self.client.get(reverse("panel:subscriptions"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Subscriptions &amp; Billing")
        self.assertContains(response, "Total Subscriptions")
        self.assertContains(response, "Active Subscriptions")
        self.assertContains(response, "Renewing Within 30 Days")
        self.assertContains(response, "Companies With Plans")
        self.assertContains(response, 'class="user-stat-value"', count=4)
        self.assertContains(response, "USD 2400.00")
        self.assertContains(response, 'name="company"')
        self.assertContains(response, 'name="status"')
        self.assertContains(response, 'name="cycle"')
        self.assertContains(response, "subscriptions-ui.css")
        self.assertIsNotNone(finders.find("control_panel/subscriptions-ui.css"))
        self.assertEqual(response.context["total_subscriptions"], 4)
        self.assertEqual(response.context["active_subscriptions"], 2)
        self.assertEqual(response.context["upcoming_renewals"], 1)
        self.assertEqual(response.context["subscriber_companies"], 2)

    def test_combinable_company_status_cycle_and_search_filters(self):
        result = self.client.get(reverse("panel:subscriptions"), {
            "company": str(self.alpha.pk), "q": "Starter",
            "status": "Active", "cycle": "monthly",
        })
        self.assertEqual(result.context["rows"].paginator.count, 1)
        self.assertContains(result, "Starter")
        self.assertNotContains(result, "Test Pro")
        self.assertEqual(result.context["total_subscriptions"], 4)

        yearly = self.client.get(reverse("panel:subscriptions"), {"cycle": "yearly"})
        self.assertEqual(yearly.context["rows"].paginator.count, 1)
        cancelled = self.client.get(reverse("panel:subscriptions"), {"status": "Cancelled"})
        self.assertEqual(cancelled.context["rows"].paginator.count, 1)

        ignored = self.client.get(reverse("panel:subscriptions"), {
            "status": "unknown", "cycle": "unknown",
        })
        self.assertEqual(ignored.context["rows"].paginator.count, 4)
        self.assertEqual(ignored.context["status"], "")
        self.assertEqual(ignored.context["cycle"], "")

    def test_create_edit_form_renders_all_billing_fields_and_datetime_inputs(self):
        create = self.client.get(reverse("panel:subscription-create"))
        self.assertTemplateUsed(create, "control_panel/subscription_form.html")
        self.assertContains(create, "Company Assignment")
        self.assertContains(create, "Plan &amp; Billing")
        self.assertContains(create, "Subscription Schedule")
        self.assertContains(create, "Create Subscription")
        self.assertContains(create, 'name="csrfmiddlewaretoken"')
        for field in ("company", "branch", "plan_definition", "plan", "status", "billing_cycle",
                      "amount", "currency", "starts_at", "renews_at", "seats"):
            self.assertContains(create, f'name="{field}"')
        edit = self.client.get(reverse("panel:subscription-edit", args=[self.active_monthly.pk]))
        self.assertTemplateUsed(edit, "control_panel/subscription_form.html")
        self.assertContains(edit, 'type="datetime-local"')
        self.assertContains(edit, "Save Changes")

    def test_manual_subscription_creation_and_edit_keep_original_semantics(self):
        response = self.client.post(reverse("panel:subscription-create"), {
            "company": str(self.alpha.pk),
            "plan": "Standard Plus", "status": "Active",
            "billing_cycle": "monthly", "amount": "1299.00",
            "currency": "INR", "seats": "6",
        })
        self.assertRedirects(response, reverse("panel:subscriptions"))
        record = Subscription.objects.get(company=self.alpha, plan="Standard Plus")
        self.assertEqual(record.amount, Decimal("1299.00"))
        self.assertEqual(record.seats, 6)

        update = self.client.post(reverse("panel:subscription-edit", args=[record.pk]), {
            "company": str(self.alpha.pk),
            "plan": "Standard Plus", "status": "Paused",
            "billing_cycle": "monthly", "amount": "899.00",
            "currency": "INR", "seats": "4",
        })
        self.assertRedirects(update, reverse("panel:subscriptions"))
        record.refresh_from_db()
        self.assertEqual(record.status, "Paused")
        self.assertEqual(record.amount, Decimal("899.00"))

    def test_plan_definition_remains_authoritative_for_pricing_and_seats(self):
        response = self.client.post(reverse("panel:subscription-create"), {
            "company": str(self.beta.pk),
            "plan_definition": str(self.defined_plan.pk),
            "plan": "Old typed name", "status": "Active",
            "billing_cycle": "yearly", "amount": "1.00", "seats": "1",
            "currency": "INR",
        })
        self.assertRedirects(response, reverse("panel:subscriptions"))
        subscription = Subscription.objects.get(company=self.beta, plan_definition=self.defined_plan)
        self.assertEqual(subscription.plan, "Test Pro")
        self.assertEqual(subscription.amount, Decimal("8000.00"))
        self.assertEqual(subscription.seats, 14)

    def test_detail_shows_actual_plan_and_cannot_be_read_anonymously(self):
        url = reverse("panel:subscription-detail", args=[self.active_monthly.pk])
        result = self.client.get(url)
        self.assertContains(result, 'class="panel-card subscription-profile-header"')
        self.assertContains(result, "INR 499.00")
        self.assertContains(result, "Starter")
        self.assertContains(result, "Active")

        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.get(reverse("panel:subscriptions")).status_code, 302)
        self.assertEqual(self.client.get(reverse("panel:subscription-create")).status_code, 302)

    def test_other_areas_keep_their_respective_form_templates(self):
        users = self.client.get(reverse("panel:user-create"))
        self.assertTemplateUsed(users, "control_panel/user_form.html")
        company = self.client.get(reverse("panel:company-create"))
        self.assertTemplateUsed(company, "control_panel/company_form.html")
        customer = self.client.get(reverse("panel:customer-create"))
        self.assertTemplateUsed(customer, "control_panel/form.html")
