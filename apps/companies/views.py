from mimetypes import guess_type

from django.http import FileResponse, Http404
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from apps.accounts.permissions import IsCompanyAdmin
from .serializers import CompanySerializer

class MyCompanyView(generics.RetrieveUpdateAPIView):
    serializer_class = CompanySerializer
    permission_classes = [IsCompanyAdmin]

    def get_object(self):
        # The platform-admin flag never selects a different tenant via query string.
        return self.request.user.company


class MyCompanyLogoView(APIView):
    """Stream the signed-in user's own company logo without exposing media storage.

    No artificial application-level upload size limit is introduced here:
    Django's existing ImageField validates the image on PATCH /company/me.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        company = getattr(request.user, "company", None)
        if not company or not company.logo_image:
            raise Http404("Company logo not found.")

        try:
            image = company.logo_image.open("rb")
        except (FileNotFoundError, OSError, ValueError):
            raise Http404("Company logo not found.")

        content_type = guess_type(company.logo_image.name)[0] or "application/octet-stream"
        result = FileResponse(image, content_type=content_type)
        result["Cache-Control"] = "private, no-store"
        result["X-Content-Type-Options"] = "nosniff"
        return result
