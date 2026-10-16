from rest_framework import generics
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission, SAFE_METHODS
from apps.accounts.permissions import IsCompanyAdmin, can_delegate, is_company_owner, user_has_permission
from .models import Role
from .assignment import assignable_role
from .serializers import RoleSerializer

class CanReadRolesOwnerCanWrite(BasePermission):
    message = "Only the company owner can create, edit or delete roles."

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            if request.query_params.get('assignable') == '1':
                return user_has_permission(request.user, 'staff.create') or user_has_permission(request.user, 'staff.edit') or IsCompanyAdmin().has_permission(request, view)
            return IsCompanyAdmin().has_permission(request, view)
        return is_company_owner(request.user) and user_has_permission(request.user, 'company.manage')


class RoleListCreateView(generics.ListCreateAPIView):
    serializer_class = RoleSerializer
    permission_classes = [CanReadRolesOwnerCanWrite]

    def get_queryset(self):
        company = self.request.user.company
        roles = Role.objects.filter(company=company, is_system=False)
        if self.request.query_params.get('assignable') == '1':
            allowed = [role.pk for role in roles if assignable_role(role, self.request.user)]
            roles = roles.filter(pk__in=allowed)
        return roles

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company, is_system=False)

class RoleDetailView(generics.RetrieveUpdateDestroyAPIView):
    def get_object(self):
        role = super().get_object()
        if self.request.method not in {"GET", "HEAD", "OPTIONS"} and not can_delegate(self.request.user, role.permissions):
            raise PermissionDenied("You cannot modify a role with greater permissions.")
        return role

    serializer_class = RoleSerializer
    permission_classes = [CanReadRolesOwnerCanWrite]

    def perform_destroy(self, instance):
        from apps.employees.models import Employee
        has_staff_profile = Employee.objects.filter(
            company=instance.company, role_name__iexact=instance.name,
        ).exists()
        if instance.users.exists() or has_staff_profile:
            raise ValidationError({
                "role": "Reassign staff using this role before deleting it."
            })
        instance.delete()

    def get_queryset(self):
        return Role.objects.filter(company=self.request.user.company, is_system=False)
