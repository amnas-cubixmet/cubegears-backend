"""Keep existing employees' prior authorities across granular RBAC rollout.

Additive migration: no permissions are removed and no role identity is changed.
New roles must request every granular permission explicitly.
"""
from django.db import migrations


LEGACY_GRANTS = {
    "attendance.manage": (
        "attendance.team.view", "attendance.records.manage",
        "attendance.corrections.approve", "attendance.leave.approve",
        "attendance.overtime.approve", "attendance.shifts.manage",
        "attendance.rules.manage", "attendance.holidays.manage",
    ),
    "payroll.edit": (
        "payroll.rates.manage", "payroll.wages.finalize",
        "payroll.extras.manage", "payroll.extras.approve",
        "payroll.adjustments.manage", "payroll.process", "payroll.approve",
        "payroll.payments.record", "payroll.payments.reverse",
    ),
    "payroll.view": ("payroll.payments.view",),
}


def preserve_existing_roles(apps, schema_editor):
    Role = apps.get_model("roles", "Role")
    for role in Role.objects.filter(company__isnull=False).iterator():
        old = role.permissions
        if not isinstance(old, list) or not all(isinstance(code, str) for code in old):
            continue
        grants = list(old)
        for legacy_code, additions in LEGACY_GRANTS.items():
            if legacy_code in old:
                grants.extend(additions)
        normalized = list(dict.fromkeys(grants))
        if normalized != old:
            role.permissions = normalized
            role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [("roles", "0001_initial")]
    operations = [migrations.RunPython(preserve_existing_roles, migrations.RunPython.noop)]
