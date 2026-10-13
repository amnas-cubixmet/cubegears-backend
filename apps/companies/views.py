from rest_framework import generics
from apps.accounts.permissions import IsCompanyAdmin
from .models import Company
from .serializers import CompanySerializer

class MyCompanyView(generics.RetrieveUpdateAPIView):
    serializer_class = CompanySerializer
    permission_classes = [IsCompanyAdmin]

    def get_object(self):
        # The platform-admin flag never selects a different tenant via query string.
        return self.request.user.company
