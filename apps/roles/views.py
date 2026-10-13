from django.db.models import Q
from rest_framework import generics
from apps.accounts.permissions import IsCompanyAdmin, can_delegate
from rest_framework.exceptions import PermissionDenied
from .models import Role
from .serializers import RoleSerializer

class RoleListCreateView(generics.ListCreateAPIView):
    serializer_class = RoleSerializer
    permission_classes = [IsCompanyAdmin]

    def get_queryset(self):
        company = self.request.user.company
        return Role.objects.filter(Q(company=company) | Q(company__isnull=True))

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company, is_system=False)

class RoleDetailView(generics.RetrieveUpdateDestroyAPIView):
    def get_object(self):
        role = super().get_object()
        if self.request.method not in {"GET", "HEAD", "OPTIONS"} and not can_delegate(self.request.user, role.permissions):
            raise PermissionDenied("You cannot modify a role with greater permissions.")
        return role

    serializer_class = RoleSerializer
    permission_classes = [IsCompanyAdmin]

    def get_queryset(self):
        return Role.objects.filter(company=self.request.user.company, is_system=False)
