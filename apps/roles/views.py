from django.db import transaction
from rest_framework import generics, serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission, SAFE_METHODS
from apps.accounts.permissions import IsCompanyAdmin, can_delegate, is_company_owner, user_has_permission
from .models import Role
from .serializers import RoleSerializer
from .legacy_cleanup import eligible_legacy_signup_roles

class CanReadRolesOwnerCanWrite(BasePermission):
    message = "Only the company owner can create, edit or delete roles."

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return IsCompanyAdmin().has_permission(request, view)
        return is_company_owner(request.user) and user_has_permission(request.user, 'company.manage')


class RoleListCreateView(generics.ListCreateAPIView):
    serializer_class = RoleSerializer
    permission_classes = [CanReadRolesOwnerCanWrite]

    def get_queryset(self):
        company = self.request.user.company
        return Role.objects.filter(company=company)

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
