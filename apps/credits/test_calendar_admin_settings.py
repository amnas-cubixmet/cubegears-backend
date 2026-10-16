"""Platform-only calendar credit policy and mutual-exclusion regression tests."""
from django.test import TestCase, override_settings
from django.urls import reverse
from apps.accounts.models import User
from apps.credits.billing_cycle_forms import BillingCyclePolicyForm
from apps.credits.cycle_panel import CreditCyclePolicyForm
from apps.credits.models import BillingCyclePolicy, CreditCyclePolicy


class CalendarAdminSettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            email="calendar-platform@example.test", name="Calendar Admin",
            password="LongPassword2026!",
        )
        self.client.force_login(self.user)
        self.url = reverse("panel:calendar-credit-billing")

    def test_page_is_read_only_and_shows_calendar_usage_policy(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Calendar-month billing")
        self.assertContains(response, 'name="due_day"')
        self.assertContains(response, 'name="restrict_overdue"')
        self.assertContains(response, 'name="allow_zero_balance_usage"')
        self.assertContains(response, "No closed calendar statements yet.")

    @override_settings(
        STORAGE_MONTHLY_METERED_ENABLED=True,
        CREDIT_BILLING_CELERY_ENABLED=True,
        CREDIT_POSTPAID_FEATURE_BILLING_ENABLED=False,
    )
    def test_platform_can_enable_calendar_policy_with_due_day_five(self):
        response = self.client.post(self.url, {
            "enabled": "on", "due_day": "5",
            "restrict_overdue": "on", "allow_zero_balance_usage": "on",
        })
        self.assertEqual(response.status_code, 302)
        policy = BillingCyclePolicy.objects.get()
        self.assertTrue(policy.enabled)
        self.assertEqual(policy.due_day, 5)
        self.assertTrue(policy.restrict_overdue)
        self.assertTrue(policy.allow_zero_balance_usage)
        self.assertTrue(policy.effective_month)

    @override_settings(
        STORAGE_MONTHLY_METERED_ENABLED=True,
        CREDIT_BILLING_CELERY_ENABLED=True,
        CREDIT_POSTPAID_FEATURE_BILLING_ENABLED=False,
        CREDIT_FEATURE_ENFORCEMENT_ENABLED=True,
        CREDIT_LABEL_ONLY_ACCESS_ENABLED=True,
    )
    def test_two_billing_engines_cannot_be_activated_together(self):
        CreditCyclePolicy.objects.create(enabled=True, day_of_month=5)
        form = BillingCyclePolicyForm(data={
            "enabled": "on", "due_day": "5", "restrict_overdue": "on",
            "allow_zero_balance_usage": "on",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("5th-to-5th", str(form.errors))

        CreditCyclePolicy.objects.all().update(enabled=False)
        BillingCyclePolicy.objects.create(enabled=True, due_day=5)
        cycle_form = CreditCyclePolicyForm(data={
            "enabled": "on", "day_of_month": "5", "block_overdue": "on",
        })
        self.assertFalse(cycle_form.is_valid())
        self.assertIn("Calendar-month billing", str(cycle_form.errors))

    def test_calendar_billing_endpoint_is_platform_only(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertNotEqual(response.status_code, 200)
