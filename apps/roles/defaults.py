"""Least-privilege, company-owned staff roles for newly registered workshops."""

DEFAULT_STAFF_ROLES = (
    ("Mechanic", "MECHANIC", ["jobs.view", "jobs.edit", "attendance.self"]),
    ("QC Inspector", "QC_INSPECTOR", ["jobs.view", "jobs.edit", "attendance.self"]),
    ("Service Advisor", "SERVICE_ADVISOR", [
        "dashboard.view", "customers.view", "customers.create",
        "vehicles.view", "vehicles.create", "services.view",
        "jobs.view", "jobs.create", "jobs.edit", "attendance.self",
    ]),
    ("Parts Manager", "PARTS_MANAGER", [
        "stock.view", "stock.create", "stock.edit", "jobs.view", "attendance.self",
    ]),
    ("Accountant", "ACCOUNTANT", [
        "invoices.view", "invoices.create", "invoices.edit",
        "payments.view", "payments.create", "expenses.view",
        "expenses.create", "reports.view", "attendance.self",
    ]),
)


def create_default_staff_roles(company):
    from apps.roles.models import Role

    for name, code, permissions in DEFAULT_STAFF_ROLES:
        Role.objects.get_or_create(
            company=company,
            code=code,
            defaults={"name": name, "permissions": list(permissions),
                      "is_active": True, "is_system": False},
        )
