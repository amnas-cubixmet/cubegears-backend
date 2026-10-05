from rest_framework import generics
from apps.accounts.permissions import IsCompanyAdmin
from .models import Branch
from .serializers import BranchSerializer

class BranchListCreateView(generics.ListCreateAPIView):
    serializer_class = BranchSerializer
    permission_classes = [IsCompanyAdmin]

    def get_queryset(self):
        if self.request.user.is_superuser and self.request.query_params.get("company"):
            return Branch.objects.filter(company_id=self.request.query_params["company"])
        return Branch.objects.filter(company=self.request.user.company)

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)

class BranchDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = BranchSerializer
    permission_classes = [IsCompanyAdmin]

    def get_queryset(self):
        if self.request.user.is_superuser:
            return Branch.objects.all()
        return Branch.objects.filter(company=self.request.user.company)
