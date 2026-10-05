from rest_framework.permissions import BasePermission

class HasRolePermission(BasePermission):
    permission_code = None

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        role = getattr(request.user, "role", None)
        code = getattr(view, "permission_code", self.permission_code)
        return bool(role and role.is_active and code and role.allows(code))

class IsCompanyAdmin(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        role = getattr(request.user, "role", None)
        return bool(role and role.is_active and (role.code in {"SUPER_ADMIN", "ADMIN", "BRANCH_MANAGER"} or role.allows("company.manage")))
