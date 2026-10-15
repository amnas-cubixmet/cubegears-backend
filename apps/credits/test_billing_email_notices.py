"""Billing alert outbox is opt-in and must never alter wallet balances."""
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Company
from apps.credits.billing_notices import (
    record_billing_notice, dispatch_pending_billing_notices,
)
from apps.credits.models import (
    BillingEmailNotice, CreditWallet, CreditLedger, FeatureUsageSession,
)
from apps.credits.postpaid import settle_monthly_usage
from apps.roles.models import Role
from apps.saas.models import FeatureGroup, PlanFeature


@override_settings(
    BILLING_EMAIL_ALERTS_ENABLED=True,
    EMAIL_CELERY_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class BillingEmailOutboxTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Billing Alert Workshop", slug="billing-alert-workshop",
        )
        self.role = Role.objects.create(
            company=self.company, name="Account Billing Admin",
            code="ACCOUNT_BILLING", permissions=["billing.view", "billing.edit"],
        )
        self.viewer_role = Role.objects.create(
            company=self.company, name="Accounts Viewer",
            code="ACCOUNTS_VIEWER", permissions=["billing.view"],
        )
        self.admin = User.objects.create_user(
            name="Billing Manager", company=self.company, role=self.role,
            email="billing-manager@example.test", password="StrongTestPass123!",
        )
        self.viewer = User.objects.create_user(
            name="Viewer", company=self.company, role=self.viewer_role,
            email="viewer@example.test", password="StrongTestPass123!",
        )
        CreditWallet.objects.create(company=self.company, balance=Decimal("100.00"))

    def test_one_notice_per_qualified_recipient_and_event(self):
        n = record_billing_notice(self.company, "feature", "2026-09", "paid", Decimal("10"), feature="jobs")
        second = record_billing_notice(self.company, "feature", "2026-09", "paid", Decimal("10"), feature="jobs")
        self.assertEqual(n, 1)
        self.assertEqual(second, 0)
        self.assertEqual(BillingEmailNotice.objects.count(), 1)
        self.assertEqual(BillingEmailNotice.objects.get().recipient, self.admin.email)
        self.assertFalse(CreditLedger.objects.exists())
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("100.00"))

        result = dispatch_pending_billing_notices()
        self.assertEqual(result["sent"], 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.admin.email])
        self.assertIn("10.00", mail.outbox[0].body)
        self.assertEqual(dispatch_pending_billing_notices()["sent"], 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(BillingEmailNotice.objects.get().status, "sent")

    def test_distinct_insufficient_credit_notice(self):
        self.assertEqual(record_billing_notice(
            self.company, "storage", "2026-09", "insufficient", "30.00",
        ), 1)
        self.assertEqual(BillingEmailNotice.objects.count(), 1)
        self.assertIn("payment required", BillingEmailNotice.objects.get().subject)
        self.assertEqual(dispatch_pending_billing_notices()["sent"], 1)
        self.assertIn("insufficient wallet credits", mail.outbox[0].body)

    @override_settings(BILLING_EMAIL_ALERTS_ENABLED=False)
    def test_disabled_email_optin_never_queues_or_sends(self):
        self.assertEqual(record_billing_notice(
            self.company, "storage", "2026-09", "paid", "5.00",
        ), 0)
        self.assertFalse(BillingEmailNotice.objects.exists())
        self.assertEqual(dispatch_pending_billing_notices(), {"disabled": 1})

    def test_failed_submission_is_retryable_with_no_balance_mutation(self):
        record_billing_notice(self.company, "storage", "2026-09", "paid", "2.00")
        with patch("apps.credits.billing_notices.send_mail", side_effect=RuntimeError("SMTP unavailable")):
            result = dispatch_pending_billing_notices()
        self.assertEqual(result["failed"], 1)
        notice = BillingEmailNotice.objects.get()
        self.assertEqual(notice.status, "failed")
        self.assertEqual(notice.attempts, 1)
        self.assertGreater(notice.next_attempt_at, notice.created_at)
        self.assertEqual(dispatch_pending_billing_notices()["sent"], 0)
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("100.00"))

    @override_settings(CREDIT_POSTPAID_FEATURE_BILLING_ENABLED=True)
    def test_paid_monthly_invoice_queues_notice_without_extra_debit(self):
        group = FeatureGroup.objects.create(key="email-billing", label="Email Billing")
        feature = PlanFeature.objects.create(key="jobs", label="Job Cards", group=group)
        tz = timezone.get_default_timezone()
        FeatureUsageSession.objects.create(
            company=self.company, feature=feature,
            started_at=timezone.make_aware(datetime(2026, 9, 1), tz),
            ended_at=timezone.make_aware(datetime(2026, 10, 1), tz),
            monthly_credits=Decimal("10.00"),
            activation_credits=Decimal("0.00"),
            user_seats=1, staff_seats=0,
        )
        at = timezone.make_aware(datetime(2026, 10, 5), tz)
        first, _ = settle_monthly_usage(self.company, "feature", "2026-09", now=at)
        second, _ = settle_monthly_usage(self.company, "feature", "2026-09", now=at)
        self.assertEqual(first, "paid")
        self.assertEqual(second, "already_paid")
        self.assertEqual(CreditLedger.objects.filter(company=self.company).count(), 1)
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("90.00"))
        self.assertEqual(BillingEmailNotice.objects.filter(company=self.company).count(), 1)
