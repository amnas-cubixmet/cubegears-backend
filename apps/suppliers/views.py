"""Supplier CRUD views for both /suppliers and legacy /stock/suppliers URLs."""
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from rest_framework import response
from common.viewsets import CompanyScopedModelViewSet
from .models import Supplier
from .serializers import SupplierSerializer

class SupplierViewSet(CompanyScopedModelViewSet):
    queryset = Supplier.objects.all()
    permission_prefix = "stock"  # Preserve existing licensed inventory entitlement.
    serializer_class = SupplierSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        search = self.request.query_params.get("search", "").strip()[:120]
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(contact_person__icontains=search) |
                Q(phone__icontains=search) |
                Q(gstin__icontains=search)
            )
        state = self.request.query_params.get("active")
        if state in {"true", "false"}:
            qs = qs.filter(is_active=(state == "true"))
        return qs

    def destroy(self, request, *args, **kwargs):
        supplier = self.get_object()
        if (supplier.items.exists() or supplier.purchase_orders.exists()
                or supplier.accounting_bills.exists() or supplier.accounting_payments.exists()):
            return response.Response(
                {"message": "This supplier has inventory or purchase history. Deactivate the supplier instead."},
                status=409,
            )
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return response.Response(
                {"message": "Supplier is linked to accounting records. Deactivate instead."},
                status=409,
            )
