"""Read-only health audit for opt-in postpaid feature/storage billing.

Never creates tasks, changes balances, enables access or sends email.
Run before enabling Celery Beat, and after configuration changes.
"""
import json

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.credits.models import (
    CompanyPostpaidFeature, FeatureMeterPeriod, StorageCreditPolicy,
    StorageMeterPeriod,
)
from apps.saas.models import PlanFeature


def _status(ok, message, *, severity="blocker"):
    return {"ok": bool(ok), "level": "ok" if ok else severity, "message": message}


def audit_billing_readiness():
    feature_enabled = bool(getattr(settings, "CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED", False))
    enforce = bool(getattr(settings, "CREDIT_FEATURE_ENFORCEMENT_ENABLED", False))
    strict = bool(getattr(settings, "CREDIT_FEATURE_STRICT_GATE_ENABLED", False))
    storage_enabled = bool(getattr(settings, "STORAGE_MONTHLY_METERED_ENABLED", False))
    broker = bool(getattr(settings, "CELERY_BROKER_URL", ""))
    schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})
    email_backend = getattr(settings, "EMAIL_BACKEND", "")
    smtp = email_backend == "django.core.mail.backends.smtp.EmailBackend"
    active_storage = StorageCreditPolicy.objects.filter(is_active=True, billing_period="monthly")
    policies = list(active_storage.values("id", "credits_per_gb", "included_gb"))
    feature_states = CompanyPostpaidFeature.objects.filter(enabled=True).count()
    unregistered = sorted(set(
        CompanyPostpaidFeature.objects.filter(enabled=True)
        .exclude(feature__is_active=True).values_list("feature__key", flat=True)
    ))
    pending_storage = StorageMeterPeriod.objects.filter(settled_at__isnull=True).count()
    pending_features = FeatureMeterPeriod.objects.filter(settled_at__isnull=True).count()
    policies_valid = len(policies) == 1 and all(
        p["credits_per_gb"] > 0 and p["included_gb"] >= 0 for p in policies
    )
    checks = {
        "feature_enforcement": _status(not feature_enabled or (enforce and strict),
            "Postpaid features require both paid-access enforcement and strict gating."),
        "catalog_entries": _status(not unregistered,
            "Enabled postpaid features must be active catalog entries. Keys: " + ",".join(unregistered)),
        "storage_policy": _status(not storage_enabled or policies_valid,
            "Metered storage requires exactly one active monthly policy with a positive rate and valid free GB."),
        "redis_broker": _status(not (feature_enabled or storage_enabled) or broker,
            "A Redis broker URL is required for automatic Celery metering."),
        "feature_schedule": _status(not feature_enabled or not broker or (
            "cubegear-sample-paid-features" in schedule and
            "cubegear-settle-closed-feature-months" in schedule),
            "Both feature sampling and settlement must be scheduled."),
        "storage_schedule": _status(not storage_enabled or not broker or (
            "cubegear-sample-company-storage" in schedule and
            "cubegear-settle-closed-storage-months" in schedule),
            "Both storage sampling and settlement must be scheduled."),
        "email_provider": _status(smtp and bool(getattr(settings,"EMAIL_HOST",""))
            and bool(getattr(settings,"DEFAULT_FROM_EMAIL","")),
            "Transactional email requires SMTP provider and sender configuration.", severity="warning"),
        "legacy_storage": _status(not storage_enabled or not getattr(settings, "CELERY_RUN_LEGACY_STORAGE_BILLING", False),
            "Legacy snapshot storage charging must not run with monthly metered storage."),
    }
    return {
        "ready": all(c["ok"] for c in checks.values() if c["level"] != "warning"),
        "checks": checks,
        "counts": {
            "enabled_postpaid_features": feature_states,
            "registered_features": PlanFeature.objects.count(),
            "open_feature_periods": pending_features,
            "open_storage_periods": pending_storage,
        },
        "note": "Read-only configuration/database audit. Does not test Redis reachability, SMTP delivery, or live payment.",
    }


class Command(BaseCommand):
    help = "Read-only audit of storage/feature monthly billing, Celery, SMTP and unsent periods."

    def add_arguments(self, parser):
        parser.add_argument("--strict", action="store_true",
                            help="Exit with error when billing blockers are present.")

    def handle(self, *args, **options):
        report = audit_billing_readiness()
        self.stdout.write(json.dumps(report, indent=2))
        if options["strict"] and not report["ready"]:
            from django.core.management.base import CommandError
            raise CommandError("SaaS billing has configuration blockers; no billing was run.")
