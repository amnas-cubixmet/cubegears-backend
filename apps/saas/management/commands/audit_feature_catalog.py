"""Discover actual SaaS permission modules without inventing fake features.

Usage:
    python manage.py audit_feature_catalog
    python manage.py audit_feature_catalog --sync

Read-only by default. --sync registers missing real tenant permission prefixes.
It never grants access, charges credits or changes already registered features.
"""
import json

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.permissions import MODEL_PERMISSION_PREFIX
from apps.roles.permission_catalog import TENANT_PERMISSIONS
from apps.saas.models import FeatureGroup, PlanFeature
from apps.saas.access import FREE_TENANT_MODULES


CORE_MODULES = frozenset({
    "billing", "company", "roles",
}) | FREE_TENANT_MODULES


def discover_modules():
    """Only backend-declared tenant capabilities, excluding platform permissions."""
    permissions = set(TENANT_PERMISSIONS)
    keys = {
        permission.split(".", 1)[0]
        for permission in permissions
        if "." in permission and not permission.startswith("panel.")
    }
    keys.update(
        prefix for prefix in MODEL_PERMISSION_PREFIX.values()
        if isinstance(prefix, str)
    )
    return {
        key: {
            "key": key,
            "permissions": sorted(code for code in permissions if code.startswith(key + ".")),
            "modelBacked": sorted(model for model, prefix in MODEL_PERMISSION_PREFIX.items() if prefix == key),
            "category": (
                "core" if key in CORE_MODULES
                else "staff" if key in {"staff", "payroll", "attendance"}
                else "workshop"
            ),
            "priceable": key not in CORE_MODULES,
        }
        for key in sorted(keys)
    }


class Command(BaseCommand):
    help = "Audit true backend permission modules; --sync adds only missing PlanFeature rows."

    def add_arguments(self, parser):
        parser.add_argument("--sync", action="store_true",
                            help="Register missing discovered billable modules without changing existing ones.")

    def handle(self, *args, **options):
        modules = discover_modules()
        existing = set(PlanFeature.objects.values_list("key", flat=True))
        missing = [key for key, item in modules.items() if item["priceable"] and key not in existing]
        created = []
        if options["sync"]:
            with transaction.atomic():
                group, _ = FeatureGroup.objects.get_or_create(
                    key="discovered-modules",
                    defaults={"label": "Discovered Backend Modules", "is_active": True},
                )
                for key in missing:
                    feature, was_created = PlanFeature.objects.get_or_create(
                        key=key,
                        defaults={
                            "label": key.replace("_", " ").title(),
                            "description": "Registered from backend permission catalog; review before pricing.",
                            "group": group,
                            # Admin must review and publish explicitly.
                            "is_active": False,
                        },
                    )
                    if was_created:
                        created.append(feature.key)
        self.stdout.write(json.dumps({
            "discoveredModules": list(modules.values()),
            "registeredFeatureKeys": sorted(existing),
            "missingBillableKeys": missing,
            "createdDisabledKeys": created,
            "syncRequested": bool(options["sync"]),
            "notes": [
                "Core access is free/unmetered to avoid account lockout.",
                "Registration does not imply paid access, role permission or new functionality.",
                "Seat billing must count active login users, not Employee records.",
                "Storage billing must use actual company bytes and a separate ledger charge.",
            ],
        }, indent=2))
