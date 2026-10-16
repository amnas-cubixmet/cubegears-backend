"""Company-created staff roles; no platform, owner or wildcard overrides."""
from apps.accounts.permissions import can_delegate
from .permission_catalog import TENANT_PERMISSIONS

STAFF_PERMISSION_CODES = frozenset(TENANT_PERMISSIONS) - {'billing.view', 'billing.edit', 'company.manage'}


def assignable_role(role, actor):
    codes = role.permissions
    return bool(
        role.company_id == actor.company_id and role.company_id
        and role.is_active and not role.is_system
        and role.code.upper() not in {'SUPER_ADMIN', 'OWNER', 'ADMIN_OWNER', 'PLATFORM_ADMIN'}
        and isinstance(codes, list) and all(isinstance(code, str) and code in STAFF_PERMISSION_CODES for code in codes)
        and can_delegate(actor, codes)
    )
