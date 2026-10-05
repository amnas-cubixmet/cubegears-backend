from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

class CompanyScopedModelViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_superuser:
            company_id = self.request.query_params.get("company")
            return qs.filter(company_id=company_id) if company_id else qs
        return qs.filter(company=user.company)

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company, branch=getattr(self.request.user, "branch", None))
