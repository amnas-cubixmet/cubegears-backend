"""Inventory implemented tenant features from the RBAC permission registry.

No module is invented or auto-priced. Backend permission codes are the source
of truth; internal panel controls and system prerequisites cannot be sold.
"""
from apps.roles.permission_catalog import TENANT_PERMISSIONS
from apps.saas.models import FeatureGroup, PlanFeature
from apps.saas.access import FREE_TENANT_MODULES
from apps.saas.module_groups import WORKFLOW_PACKAGES

SYSTEM_MODULES = frozenset({"company", "roles"}) | FREE_TENANT_MODULES

DISPLAY_NAMES = {
    "attendance": "Attendance & Punches",
    "finance": "Accounts",
    "gstr": "GST Returns",
    "customers": "Customer Management",
    "expenses": "Expense Tracking",
    "invoices": "Invoices & Estimates",
    "jobs": "Job Cards",
    "payments": "Payment Tracking",
    "payroll": "Payroll",
    "reports": "Reports & Analytics",
    "services": "Service Catalogue",
    "staff": "Staff Management",
    "stock": "Stock & Inventory",
    "vehicles": "Vehicle Management",
}


def module_inventory():
    by_module = {}
    for permission in TENANT_PERMISSIONS:
        module, _, action = permission.partition(".")
        if not module or not action or module == "panel":
            continue
        # Personal attendance is always-free staff self-service, not part of
        # what the owner buys when activating Attendance & Payroll managers.
        if permission == "attendance.self":
            continue
        by_module.setdefault(module, []).append(permission)
    # Discover real permission-backed modules, then sell only related,
    # non-overlapping workflow packages. Keep each module's original RBAC.
    for group_key, details in WORKFLOW_PACKAGES.items():
        combined = set()
        for member in details["modules"]:
            combined.update(by_module.pop(member, []))
        if combined:
            by_module[group_key] = sorted(combined)
    return [
        {
            "key": module,
            "label": (WORKFLOW_PACKAGES[module]["label"] if module in WORKFLOW_PACKAGES
                      else DISPLAY_NAMES.get(module, module.replace("_", " ").title())),
            "actions": sorted(actions),
            "billable": module not in SYSTEM_MODULES,
        }
        for module, actions in sorted(by_module.items())
    ]


def sync_existing_modules():
    """Create only missing billable catalog entries; preserve platform edits."""
    group, _ = FeatureGroup.objects.get_or_create(
        key="discovered-modules", defaults={
            "label": "Discovered Workshop Modules", "sort_order": 10,
        },
    )
    created = []
    for module in module_inventory():
        if not module["billable"]:
            continue
        _, added = PlanFeature.objects.get_or_create(
            key=module["key"],
            defaults={
                "label": module["label"], "group": group,
                "description": "Existing permissions: " + ", ".join(module["actions"]),
            },
        )
        if added:
            created.append(module["key"])
    return created
