from django.db.models import Model
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.accounts.permissions import RolePermission


class CompanyScopedModelViewSet(viewsets.ModelViewSet):
    permission_classes = [RolePermission]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        # Django/platform superuser never grants an unscoped tenant queryset.
        if not user.company_id:
            return qs.none()
        return qs.filter(company_id=user.company_id)

    def _validate_related_tenants(self, validated_data):
        """Reject foreign-key objects belonging to another company before save.

        DRF can resolve a writable PrimaryKeyRelatedField using a queryset of
        *all* tenants; checking just the created object's company is not enough.
        Every standard create/update path executes this guard even if a child
        viewset has its own perform_create/perform_update implementation.
        """
        tenant_id = self.request.user.company_id

        def validate(value, field):
            if isinstance(value, Model) and hasattr(value, "company_id"):
                if value.company_id != tenant_id:
                    raise ValidationError({field: "Related record does not belong to your company."})
            elif isinstance(value, dict):
                for nested_name, nested_value in value.items():
                    validate(nested_value, f"{field}.{nested_name}")
            elif isinstance(value, (list, tuple)):
                for nested_value in value:
                    validate(nested_value, field)

        for field_name, field_value in validated_data.items():
            validate(field_value, field_name)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self._validate_related_tenants(serializer.validated_data)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self._validate_related_tenants(serializer.validated_data)
        self.perform_update(serializer)
        if getattr(instance, "_prefetched_objects_cache", None):
            instance._prefetched_objects_cache = {}
        return Response(serializer.data)

    def perform_create(self, serializer):
        serializer.save(
            company=self.request.user.company,
            branch=getattr(self.request.user, "branch", None),
        )
