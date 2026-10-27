from rest_framework.permissions import BasePermission, SAFE_METHODS


MODEL_PERMISSION_PREFIX = {
    "customer": "customers",
    "customeractivity": "customers",
    "customerreminder": "customers",
    "vehicle": "vehicles",
    "service": "services",
    "servicecategory": "services",
    "job": "jobs",
    "jobestimate": "jobs",
    "jobpart": "jobs",
    "jobphoto": "jobs",
    "jobactivity": "jobs",
    "stockitem": "stock",
    "stockcategory": "stock",
    "stockmovement": "stock",
    "supplier": "stock",
    "purchaseorder": "stock",
    "stocktransfer": "stock",
    "stockaudit": "stock",
    "invoice": "invoices",
    "invoiceitem": "invoices",
    "ewaybill": "invoices",
    "payment": "payments",
    "expense": "expenses",
    "employee": "staff",
    "employeedocument": "staff",
    "employeeactivity": "staff",
    "team": "staff",
    "shift": "staff",
    "attendancerecord": "attendance",
    "leaverequest": "attendance",
    "overtimerequest": "attendance",
    "holiday": "attendance",
    "attendancerule": "attendance",
    "punchcorrection": "attendance",
    "leavetype": "attendance",
    "salarystructure": "payroll",
    "salaryadvance": "payroll",
    "payrollrun": "payroll",
    "payslip": "payroll",
    "incentive": "payroll",
    "notification": "notifications",
    "subscription": "billing",
    "storageusage": "storage",
    "documenttemplate": "settings",
    "securityevent": "settings",
    "mediafile": "storage",
}


def is_company_owner(user):
    return bool(user and user.is_authenticated and user.is_active
                and user.company_id and user.company.is_active
                and user.company.owner_id == user.pk
                and user.role_id and user.role.is_active
                and user.role.company_id == user.company_id)


class IsCompanyOwner(BasePermission):
    message = "Only the company account owner can access the SaaS account."

    def has_permission(self, request, view):
        return is_company_owner(request.user)


def user_has_permission(user, permission_code):
    if (
        not user
        or not user.is_authenticated
        or not user.is_active
        or not isinstance(permission_code, str)
    ):
        return False

    if permission_code.startswith("panel."):
        # The SaaS platform is not a workshop tenant. Platform superusers and
        # explicitly delegated platform staff must have NO company attached.
        # A workshop's company-level wildcard must never grant platform access.
        if user.company_id or not user.is_staff:
            return False
        if user.is_superuser:
            return True
        role = getattr(user, "role", None)
        if not role or not role.is_active or role.company_id is not None:
            return False
    else:
        # Existing tenant RBAC: never cross company boundaries, even when a
        # platform administrator is logged in.
        if not user.company_id or not user.company or not user.company.is_active:
            return False
        role = getattr(user, "role", None)
        if not role or not role.is_active or role.company_id != user.company_id:
            return False

    # The verified company account owner is the canonical tenant administrator.
    # Stored role permissions can become incomplete after catalog migrations; they
    # must not remove the owner's authority. Feature entitlements, debt holds
    # and cross-company restrictions below still apply to the owner.
    verified_owner = is_company_owner(user) if user.company_id else False
    # Ownership cannot be delegated through a wildcard or billing role permission.
    if permission_code.startswith("billing."):
        return verified_owner

    if user.company_id:
        from apps.credits.monthly_policy import billing_status
        if billing_status(user.company)['blocked']:
            return False

    if not verified_owner:
        permissions = role.permissions or []
        if not isinstance(permissions, list) or any(not isinstance(code, str) for code in permissions):
            return False
        role_allows = "*" in permissions or permission_code in permissions
        if not role_allows:
            return False
    if not permission_code.startswith("panel."):
        # A persisted, past-due invoice locks paid/workshop APIs server-side;
        # hiding React navigation alone would be an authorization bypass.
        # Billing/top-up and the OWNER's storage usage/file audit remain
        # available for repayment and data export after a hold.
        if not permission_code.startswith("billing.") and not (
            is_company_owner(user) and permission_code.startswith(
                ("storage.", "company.")
            )
        ):
            from apps.credits.cycle_billing import company_is_overdue
            if company_is_overdue(user.company):
                return False
        from apps.saas.access import feature_allowed
        return feature_allowed(user, permission_code)
    return True


def user_has_permissions(user, permission_codes):
    if isinstance(permission_codes, str):
        return user_has_permission(user, permission_codes)

    codes = list(permission_codes or [])
    return bool(codes) and all(user_has_permission(user, code) for code in codes)


def infer_permission_prefix(view):
    explicit = getattr(view, "permission_prefix", None)
    if explicit:
        return explicit

    queryset = getattr(view, "queryset", None)
    model = getattr(queryset, "model", None)
    if model is None:
        try:
            queryset = view.get_queryset()
            model = getattr(queryset, "model", None)
        except Exception:
            model = None

    if model is None:
        return None

    return MODEL_PERMISSION_PREFIX.get(model._meta.model_name, model._meta.model_name)


class RolePermission(BasePermission):
    """
    Tenant RBAC permission.

    Supports:
      permission_code = "customers.view"
      permission_map = {"GET": "customers.view", "POST": "customers.create"}
      permission_prefix = "customers" for automatic CRUD mapping.
    """

    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated or not request.user.is_active:
            return False

        action_map = getattr(view, "action_permission_map", None) or {}
        action_rule = action_map.get(getattr(view, "action", None))
        if isinstance(action_rule, dict):
            code = action_rule.get(request.method)
        else:
            code = action_rule

        permission_map = getattr(view, "permission_map", None) or {}
        if not code:
            code = permission_map.get(request.method)

        if not code:
            code = getattr(view, "permission_code", None)

        if not code:
            prefix = infer_permission_prefix(view)
            if not prefix:
                return False

            if request.method in SAFE_METHODS:
                action = "view"
            elif request.method == "POST":
                action = "create"
            elif request.method in {"PUT", "PATCH"}:
                action = "edit"
            elif request.method == "DELETE":
                action = "delete"
            else:
                return False

            code = f"{prefix}.{action}"

        return user_has_permissions(request.user, code)


class HasRolePermission(RolePermission):
    permission_code = None


class IsCompanyAdmin(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated or not request.user.is_active:
            return False
        if request.user.is_superuser:
            return True

        return user_has_permission(request.user, "company.manage")


def can_delegate(user, permissions):
    """A manager cannot grant authority they do not possess."""
    if not isinstance(permissions, list) or any(not isinstance(code, str) for code in permissions):
        return False
    # Platform superusers can delegate tenant roles without receiving tenant
    # API permissions themselves. Tenant access checks remain company-scoped.
    if (user and user.is_authenticated and user.is_active and user.is_staff
            and user.is_superuser and user.company_id is None):
        return True
    return all(user_has_permission(user, code) for code in permissions)


def panel_permission(user, resource, action="view"):
    return user_has_permission(user, f"panel.{resource}.{action}")
