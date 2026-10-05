from rest_framework import decorators,response,status
from common.viewsets import CompanyScopedModelViewSet
from .models import Service,ServiceCategory
from .serializers import ServiceSerializer,ServiceCategorySerializer

class ServiceViewSet(CompanyScopedModelViewSet):
    queryset=Service.objects.select_related("category").all()
    serializer_class=ServiceSerializer

class ServiceCategoryViewSet(CompanyScopedModelViewSet):
    queryset=ServiceCategory.objects.all()
    serializer_class=ServiceCategorySerializer

    @decorators.action(detail=True,methods=["post"],url_path="types")
    def add_type(self,request,pk=None):
        obj=self.get_object()
        types=list(obj.types or [])
        item={"id":f"TYPE-{len(types)+1}",**request.data}
        types.append(item)
        obj.types=types
        obj.save(update_fields=["types","updated_at"])
        return response.Response(item,status=status.HTTP_201_CREATED)
