from django.db import transaction
from common.viewsets import CompanyScopedModelViewSet
from .models import Payment
from .serializers import PaymentSerializer

class PaymentViewSet(CompanyScopedModelViewSet):
    queryset=Payment.objects.select_related("customer","invoice","recorded_by").all()
    serializer_class=PaymentSerializer
    @transaction.atomic
    def perform_create(self,serializer):
        payment=serializer.save(company=self.request.user.company,branch=self.request.user.branch,recorded_by=self.request.user)
        if payment.invoice_id:
            inv=payment.invoice
            inv.paid += payment.amount
            inv.balance=max(0,inv.total-inv.paid)
            if inv.balance<=0: inv.status="Paid"
            inv.save(update_fields=["paid","balance","status","updated_at"])
