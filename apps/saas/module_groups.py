"""Canonical chargeable feature for two separately permissioned staff modules.

Permission codes and old purchases remain intact. Only the feature-pricing
catalog merges Attendance and Payroll into one new paid entitlement.
"""
ATTENDANCE_PAYROLL_FEATURE = "attendance_payroll"
ATTENDANCE_PAYROLL_MODULES = frozenset({"attendance", "payroll"})


def billed_feature_keys(permission_module):
    """Recognize the combined product and existing individual entitlements."""
    if permission_module in ATTENDANCE_PAYROLL_MODULES:
        return (permission_module, ATTENDANCE_PAYROLL_FEATURE)
    return (permission_module,)


def expand_entitlements(keys):
    active = set(keys)
    if ATTENDANCE_PAYROLL_FEATURE in active:
        active.update(ATTENDANCE_PAYROLL_MODULES)
    return active


def legacy_only_workforce_bundle(keys):
    """Legacy single-module bundles remain visible only to past purchasers."""
    return bool(keys) and set(keys) <= ATTENDANCE_PAYROLL_MODULES
