from common.viewsets import CompanyScopedModelViewSet
from .models import Service,ServiceCategory
from .serializers import ServiceSerializer,ServiceCategorySerializer
class ServiceViewSet(CompanyScopedModelViewSet):
    queryset=Service.objects.select_related("category").all(); serializer_class=ServiceSerializer
class ServiceCategoryViewSet(CompanyScopedModelViewSet):
    queryset=ServiceCategory.objects.all(); serializer_class=ServiceCategorySerializer
