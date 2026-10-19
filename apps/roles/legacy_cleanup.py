"""One-time, owner-reviewed cleanup for roles seeded by an older signup flow.

Do not invoke these definitions during company registration. Legacy records
have no creation-source marker, so require an *exact* original preset match,
no staff assignments, and explicit per-role owner confirmation.
"""
from apps.employees.models import Employee
from apps.roles.models import Role

# Frozen snapshot of the FIVE preset roles created by older signup versions.
# Name, code, permissions and active status must be unchanged.
LEGACY_SIGNUP_PRESETS = {
    "MECHANIC": ("Mechanic", ("jobs.view", "jobs.edit", "attendance.self")),
    "QC_INSPECTOR": ("QC Inspector", ("jobs.view", "jobs.edit", "attendance.self")),
    "SERVICE_ADVISOR": (
        "Service Advisor",
        ("dashboard.view", "customers.view", "customers.create",
         "vehicles.view", "vehicles.create", "services.view",
         "jobs.view", "jobs.create", "jobs.edit", "attendance.self"),
    ),
    "PARTS_MANAGER": (
        "Parts Manager",
        ("stock.view", "stock.create", "stock.edit", "jobs.view", "attendance.self"),
    ),
    "ACCOUNTANT": (
        "Accountant",
        ("invoices.view", "invoices.create", "invoices.edit",
         "payments.view", "payments.create", "expenses.view",
         "expenses.create", "reports.view", "attendance.self"),
    ),
}


def eligible_legacy_signup_roles(company, *, lock=False):
    """Return removable rows only; nothing is deleted by discovery."""
    queryset = Role.objects.filter(
        company=company, is_system=False, is_active=True,
        code__in=LEGACY_SIGNUP_PRESETS.keys(),
    )
    if lock:
        queryset = queryset.select_for_update()
    candidates = []
    for role in queryset:
        expected_name, expected_permissions = LEGACY_SIGNUP_PRESETS[role.code]
        if role.name != expected_name:
            continue
        if not isinstance(role.permissions, list) or set(role.permissions) != set(expected_permissions):
            continue
        # Both login accounts and staff profiles WITHOUT login can hold a role.
        if role.users.exists():
            continue
        if Employee.objects.filter(
            company=company, role_name__iexact=role.name,
        ).exists():
            continue
        candidates.append(role)
    return candidates
