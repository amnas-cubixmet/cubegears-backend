import json
from io import StringIO
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.credits.management.commands.audit_saas_billing import audit_billing_readiness
from apps.credits.models import CreditLedger, CreditWallet, StorageCreditPolicy


class SaaSBillingReadinessTests(TestCase):
    def test_default_disabled_flags_read_only(self):
        before = (CreditWallet.objects.count(), CreditLedger.objects.count())
        out = StringIO()
        call_command("audit_saas_billing", stdout=out)
        report = json.loads(out.getvalue())
        self.assertTrue(report["ready"])
        self.assertEqual(before, (CreditWallet.objects.count(), CreditLedger.objects.count()))

    @override_settings(
        CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=True,
        CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
        CREDIT_FEATURE_STRICT_GATE_ENABLED=False,
        CELERY_BROKER_URL="",
        CELERY_BEAT_SCHEDULE={},
    )
    def test_unsafe_feature_enforcement_is_a_blocker(self):
        report = audit_billing_readiness()
        self.assertFalse(report["ready"])
        self.assertFalse(report["checks"]["feature_enforcement"]["ok"])
        self.assertFalse(report["checks"]["redis_broker"]["ok"])
        with self.assertRaises(CommandError):
            call_command("audit_saas_billing", "--strict", stdout=StringIO())

    @override_settings(
        STORAGE_MONTHLY_METERED_ENABLED=True,
        CELERY_BROKER_URL="redis://localhost:6379/0",
        CELERY_BEAT_SCHEDULE={
            "cubegear-sample-company-storage": {},
            "cubegear-settle-closed-storage-months": {},
        },
    )
    def test_monthly_storage_requires_valid_policy(self):
        self.assertFalse(audit_billing_readiness()["ready"])
        StorageCreditPolicy.objects.create(
            billing_period="monthly", included_gb=Decimal("1.0"),
            credits_per_gb=Decimal("10.0"), is_active=True,
        )
        self.assertTrue(audit_billing_readiness()["ready"])
        StorageCreditPolicy.objects.create(
            billing_period="monthly", included_gb=Decimal("0"),
            credits_per_gb=Decimal("15"), is_active=True,
        )
        self.assertFalse(audit_billing_readiness()["ready"])

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend", EMAIL_HOST="")
    def test_email_warning_is_not_an_invoice_blocker(self):
        report = audit_billing_readiness()
        self.assertTrue(report["ready"])
        self.assertEqual(report["checks"]["email_provider"]["level"], "warning")
