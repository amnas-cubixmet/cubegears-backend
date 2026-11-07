"""Live company-owned vehicle directory, history and non-destructive archive."""

from django.db import transaction
from django.db.models import Q
from rest_framework import decorators, response, status

from common.viewsets import CompanyScopedModelViewSet
from apps.customers.models import CustomerActivity
from .models import Vehicle
from .serializers import VehicleSerializer


class VehicleViewSet(CompanyScopedModelViewSet):
    queryset = Vehicle.objects.select_related("customer", "branch").all()
    serializer_class = VehicleSerializer
    action_permission_map = {
        "history": "jobs.view",
        "restore": "vehicles.edit",
    }

    def get_queryset(self):
        qs = super().get_queryset()
        q = (self.request.query_params.get("search") or "").strip()[:120]
        if q:
            qs = qs.filter(
                Q(registration__icontains=q)
                | Q(make__icontains=q)
                | Q(model__icontains=q)
                | Q(vin__icontains=q)
                | Q(customer__name__icontains=q)
                | Q(customer__phone__icontains=q)
            )
        vehicle_status = self.request.query_params.get("status")
        if vehicle_status and vehicle_status.lower() != "all":
            qs = qs.filter(status__iexact=vehicle_status)
        customer_id = self.request.query_params.get("customer")
        if customer_id:
            import uuid
            try:
                qs = qs.filter(customer_id=uuid.UUID(customer_id))
            except (ValueError, AttributeError, TypeError):
                qs = qs.none()
        return qs

    def perform_create(self, serializer):
        customer = serializer.validated_data["customer"]
        branch = customer.branch or self.request.user.branch
        serializer.save(
            company=self.request.user.company,
            branch=branch,
            customer=customer,
        )

    def _set_archived(self, request, vehicle, archived):
        old = vehicle.status
        new = "Archived" if archived else "Active"
        if old != new:
            vehicle.status = new
            vehicle.save(update_fields=["status", "updated_at"])
            CustomerActivity.objects.create(
                company=vehicle.company, branch=vehicle.branch,
                customer=vehicle.customer,
                activity_type="Vehicle Archived" if archived else "Vehicle Restored",
                description=f"{vehicle.registration} status changed to {new}.",
                actor=request.user,
                metadata={"vehicleId": str(vehicle.pk), "previous": old, "status": new},
            )
        return vehicle

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        self._set_archived(request, self.get_object(), True)
        return response.Response(status=status.HTTP_204_NO_CONTENT)

    @transaction.atomic
    @decorators.action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        vehicle = self.get_object()
        if vehicle.customer.status == "archived":
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"customer": "Restore the customer before restoring their vehicle."})
        return response.Response(
            self.get_serializer(self._set_archived(request, vehicle, False)).data
        )

    @decorators.action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        from apps.jobs.models import Job
        from apps.jobs.serializers import JobSerializer
        jobs = Job.objects.filter(
            company_id=request.user.company_id, vehicle=self.get_object()
        ).order_by("-created_at")
        return response.Response(JobSerializer(jobs, many=True).data)
