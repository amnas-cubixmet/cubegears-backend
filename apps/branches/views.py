from rest_framework import generics
from apps.saas.access import enforce_branch_limit
from apps.accounts.permissions import IsCompanyAdmin
from .models import Branch
from .serializers import BranchSerializer

class BranchListCreateView(generics.ListCreateAPIView):
    serializer_class = BranchSerializer
    permission_classes = [IsCompanyAdmin]

    def get_queryset(self):
        return Branch.objects.filter(company=self.request.user.company)

    def perform_create(self, serializer):
        enforce_branch_limit(self.request.user.company)
        serializer.save(company=self.request.user.company)

class BranchDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = BranchSerializer
    permission_classes = [IsCompanyAdmin]

    def perform_update(self, serializer):
        if not serializer.instance.is_active and serializer.validated_data.get("is_active") is True:
            enforce_branch_limit(self.request.user.company)
        serializer.save()

    def get_queryset(self):
        return Branch.objects.filter(company=self.request.user.company)
