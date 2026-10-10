from rest_framework import decorators,response,status
from django.db.models import Q
from common.viewsets import CompanyScopedModelViewSet
from .models import Service,ServiceCategory
from .serializers import ServiceSerializer,ServiceCategorySerializer

class ServiceViewSet(CompanyScopedModelViewSet):
    queryset=Service.objects.select_related("category").all()
    serializer_class=ServiceSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        query = str(self.request.query_params.get("search") or "").strip()
        if query:
            qs = qs.filter(
                Q(name__icontains=query) |
                Q(code__icontains=query) |
                Q(description__icontains=query)
            )
        return qs

class ServiceCategoryViewSet(CompanyScopedModelViewSet):
    queryset=ServiceCategory.objects.all()
    serializer_class=ServiceCategorySerializer
    action_permission_map={"add_type": "services.edit"}

    @decorators.action(detail=True,methods=["post"],url_path="types")
    def add_type(self,request,pk=None):
        obj=self.get_object()
        types=list(obj.types or [])
        item={"id":f"TYPE-{len(types)+1}",**request.data}
        types.append(item)
        obj.types=types
        obj.save(update_fields=["types","updated_at"])
        return response.Response(item,status=status.HTTP_201_CREATED)
