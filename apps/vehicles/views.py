from django.db.models import Q
from rest_framework import decorators,response
from common.viewsets import CompanyScopedModelViewSet
from .models import Vehicle
from .serializers import VehicleSerializer

class VehicleViewSet(CompanyScopedModelViewSet):
    queryset=Vehicle.objects.select_related("customer").all()
    serializer_class=VehicleSerializer

    def get_queryset(self):
        qs=super().get_queryset()
        q=self.request.query_params.get("search")
        if q: qs=qs.filter(Q(registration__icontains=q)|Q(make__icontains=q)|Q(model__icontains=q)|Q(vin__icontains=q)|Q(customer__name__icontains=q))
        return qs

    def perform_create(self, serializer):
        customer=serializer.validated_data.get("customer")
        if customer and customer.company_id != self.request.user.company_id and not self.request.user.is_superuser:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Customer belongs to another company.")
        serializer.save(company=self.request.user.company,branch=self.request.user.branch)

    @decorators.action(detail=True,methods=["get"])
    def history(self,request,pk=None):
        from apps.jobs.models import Job
        from apps.jobs.serializers import JobSerializer
        return response.Response(JobSerializer(Job.objects.filter(vehicle=self.get_object()).order_by("-created_at"),many=True).data)
