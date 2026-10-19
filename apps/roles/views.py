from django.db import transaction
from rest_framework import generics, serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission, SAFE_METHODS
from apps.accounts.permissions import IsCompanyAdmin, is_company_owner, user_has_permission
from .models import Role
from .assignment import assignable_role
from .serializers import RoleSerializer
from .legacy_cleanup import eligible_legacy_signup_roles

class CanReadRolesOwnerCanWrite(BasePermission):
    message = "Only the company owner can create, edit or delete roles."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated or not request.user.company_id:
            return False
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
    # The permission class already restricts writes to the verified owner.
    # Do not compare historic role permissions with the owner's *current*
    # paid entitlements: that blocks deleting roles containing expired modules.
    # Protected system roles remain excluded by get_queryset().
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


class LegacySignupRolesCleanupView(APIView):
    """Explicit company-owner review and cleanup; never run on login/signup."""
    permission_classes = [CanReadRolesOwnerCanWrite]

    def _require_owner(self, request):
        if not (is_company_owner(request.user)
                and user_has_permission(request.user, "company.manage")):
            raise PermissionDenied("Only the company owner can clean up legacy roles.")

    def get(self, request):
        self._require_owner(request)
        candidates = eligible_legacy_signup_roles(request.user.company)
        return Response({
            "roles": [
                {"id": str(role.pk), "name": role.name, "code": role.code}
                for role in candidates
            ],
            "notice": (
                "Only unchanged, unassigned legacy signup presets are eligible. "
                "Review each role before confirming. Existing custom roles and "
                "roles assigned to staff are never removed."
            ),
        })

    @transaction.atomic
    def post(self, request):
        self._require_owner(request)
        requested = serializers.ListField(
            child=serializers.UUIDField(), allow_empty=False, max_length=5
        ).run_validation(request.data.get("roleIds"))
        requested_ids = {str(value) for value in requested}
        if len(requested_ids) != len(requested):
            raise ValidationError({"roleIds": "Duplicate role IDs are not allowed."})

        candidates = eligible_legacy_signup_roles(request.user.company, lock=True)
        eligible_by_id = {str(role.id): role for role in candidates}
        if not requested_ids.issubset(eligible_by_id):
            raise ValidationError({
                "roleIds": "Some roles have changed or are assigned to staff. Refresh the review before continuing."
            })
        removed = [
            {"id": str(role.pk), "name": role.name}
            for role in candidates if str(role.pk) in requested_ids
        ]
        # QuerySet deletion is constrained to this company and explicitly
        # selected, freshly verified rows; no other tenant records are touched.
        Role.objects.filter(
            company=request.user.company,
            pk__in=[row["id"] for row in removed],
            is_system=False,
        ).delete()
        return Response({"removed": removed, "count": len(removed)})
