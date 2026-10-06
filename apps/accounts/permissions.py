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
    "team": "staff",
    "shift": "staff",
    "skill": "staff",
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


def user_has_permission(user, permission_code):
    if not user or not user.is_authenticated:
        return False

    # Django/platform superusers bypass tenant RBAC.
    if user.is_superuser:
        return True

    role = getattr(user, "role", None)
    if not role or not role.is_active:
        return False

    permissions = role.permissions or []
    return "*" in permissions or permission_code in permissions


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
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.is_superuser:
            return True

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
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True

        role = getattr(request.user, "role", None)
        if not role or not role.is_active:
            return False

        return bool(role.allows("company.manage"))
