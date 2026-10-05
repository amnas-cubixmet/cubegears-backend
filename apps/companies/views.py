from rest_framework import generics
from apps.accounts.permissions import IsCompanyAdmin
from .models import Company
from .serializers import CompanySerializer

class MyCompanyView(generics.RetrieveUpdateAPIView):
    serializer_class = CompanySerializer
    permission_classes = [IsCompanyAdmin]

    def get_object(self):
        if self.request.user.is_superuser and self.request.query_params.get("company"):
            return Company.objects.get(pk=self.request.query_params["company"])
        return self.request.user.company
