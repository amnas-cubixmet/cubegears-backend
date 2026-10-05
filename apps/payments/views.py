from django.db import transaction
from rest_framework.exceptions import ValidationError
from common.viewsets import CompanyScopedModelViewSet
from .models import Payment
from .serializers import PaymentSerializer

class PaymentViewSet(CompanyScopedModelViewSet):
    queryset=Payment.objects.select_related("customer","invoice","recorded_by").all()
    serializer_class=PaymentSerializer
    @transaction.atomic
    def perform_create(self,serializer):
        company=self.request.user.company
        customer=serializer.validated_data.get("customer")
        invoice=serializer.validated_data.get("invoice")
        if customer and customer.company_id != company.id:
            raise ValidationError({"customer":"Customer belongs to another company."})
        if invoice and invoice.company_id != company.id:
            raise ValidationError({"invoice":"Invoice belongs to another company."})
        if invoice and customer and invoice.customer_id != customer.id:
            raise ValidationError({"invoice":"Invoice does not belong to the selected customer."})
        payment=serializer.save(company=company,branch=self.request.user.branch,recorded_by=self.request.user)
        if payment.invoice_id:
            inv=payment.invoice
            inv.paid += payment.amount
            inv.balance=max(0,inv.total-inv.paid)
            if inv.balance<=0: inv.status="Paid"
            inv.save(update_fields=["paid","balance","status","updated_at"])
