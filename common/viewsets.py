from rest_framework import viewsets

from apps.accounts.permissions import RolePermission


class CompanyScopedModelViewSet(viewsets.ModelViewSet):
    permission_classes = [RolePermission]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        # Never expose every company's records through a query parameter or
        # the Django platform superuser flag. Tenant APIs are always scoped.
        if not user.company_id:
            return qs.none()
        return qs.filter(company_id=user.company_id)

    def perform_create(self, serializer):
        serializer.save(
            company=self.request.user.company,
            branch=getattr(self.request.user, "branch", None),
        )
