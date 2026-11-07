"""Authoritative workflow-first SaaS feature catalogue.

These are PURCHASE groupings, not role permissions. Underlying module
permissions, service endpoints, ledgers and historical entitlements survive.
The groups are non-overlapping, so no module is sold in two new packages.
"""
from collections import OrderedDict

WORKFLOW_PACKAGES = OrderedDict((
    ("workshop_operations", {
        "label": "Workshop Operations",
        "description": "Customers, vehicles, services, job cards, inventory/suppliers, invoices and customer updates.",
        "modules": ("customers", "vehicles", "services", "jobs", "stock", "invoices", "notifications"),
        "flows": ("Customer → Vehicle → Job Card → Parts → Estimate → Invoice → Customer Updates",),
        "dependencies": (),
    }),
    ("accounts_tax", {
        "label": "Accounts & Tax",
        "description": "Cash, UPI and bank payments, expense ledger, accounting and GST reports.",
        "modules": ("payments", "expenses", "finance", "gstr"),
        "flows": ("Invoice/Expense/Payroll → Payment → Ledger → GST and accounting",),
        "dependencies": ("workshop_operations",),
    }),
    ("attendance_payroll", {
        "label": "Attendance & Payroll",
        "description": "Shift attendance, leave, overtime, daily wages and payroll approvals/payments.",
        "modules": ("attendance", "payroll"),
        "flows": ("Attendance & Shifts → Hours/OT → Wage Calculation → Payroll Approval",),
        "dependencies": (),
    }),
    ("business_insights", {
        "label": "Reports & Analytics",
        "description": "Workshop analytics using only the data and underlying permissions already available.",
        "modules": ("reports",),
        "flows": ("Workshop and finance activity → Authorized reports",),
        "dependencies": (),
    }),
))

# Storage remains an independent, usage-metered platform billing product;
# the owner can inspect storage from SaaS Account without purchasing this module.
INDEPENDENT_MODULES = frozenset({"storage"})
GROUP_KEYS = frozenset(WORKFLOW_PACKAGES)
ALL_GROUPED_MODULES = frozenset(
    module for package in WORKFLOW_PACKAGES.values() for module in package["modules"]
)


def billed_feature_keys(permission_module):
    """One package plus any pre-existing individual module subscription."""
    key = next(
        (key for key, info in WORKFLOW_PACKAGES.items()
         if permission_module in info["modules"]), None
    )
    return (permission_module, key) if key else (permission_module,)


def expand_entitlements(keys):
    """Expand a paid package for access checks without changing role grants."""
    active = set(keys)
    for key, info in WORKFLOW_PACKAGES.items():
        if key in active:
            active.update(info["modules"])
    return active


def legacy_only_workforce_bundle(keys):
    """Compatibility alias for earlier feature-merging code."""
    return legacy_individual_feature_bundle(keys)


def legacy_individual_feature_bundle(keys):
    """Hide obsolete standalone products from *new* customers only."""
    return bool(keys) and set(keys) <= ALL_GROUPED_MODULES and not (set(keys) & GROUP_KEYS)


def package_for(module):
    return next((key for key, info in WORKFLOW_PACKAGES.items()
                 if module in info["modules"]), None)


def package_members(key):
    return WORKFLOW_PACKAGES[key]["modules"] if key in WORKFLOW_PACKAGES else (key,)


def package_metadata(key):
    details = WORKFLOW_PACKAGES.get(key)
    if not details:
        return None
    return {
        "packageKey": key,
        "includedModules": list(details["modules"]),
        "workflowDependencies": list(details["dependencies"]),
        "workflowPaths": list(details["flows"]),
    }
