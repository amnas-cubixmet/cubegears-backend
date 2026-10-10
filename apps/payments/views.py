from decimal import Decimal
from uuid import UUID

from django.db import transaction
from rest_framework.exceptions import ValidationError

from common.viewsets import CompanyScopedModelViewSet
from apps.invoices.models import Invoice
from .models import Payment
from .serializers import PaymentSerializer


class PaymentViewSet(CompanyScopedModelViewSet):
    queryset = Payment.objects.select_related("customer", "invoice", "recorded_by").all()
    serializer_class = PaymentSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        invoice_id = self.request.query_params.get("invoice")
        if invoice_id:
            try:
                UUID(str(invoice_id))
            except (ValueError, TypeError, AttributeError):
                return qs.none()
            qs = qs.filter(invoice_id=invoice_id)
        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        company = self.request.user.company
        customer = serializer.validated_data.get("customer")
        invoice = serializer.validated_data.get("invoice")
        amount = serializer.validated_data.get("amount")

        if amount is None or amount <= Decimal("0"):
            raise ValidationError({"amount": "Payment must be greater than zero."})
        if customer and customer.company_id != company.id:
            raise ValidationError({"customer": "Customer belongs to another company."})
        if invoice and invoice.company_id != company.id:
            raise ValidationError({"invoice": "Invoice belongs to another company."})
        if invoice and customer and invoice.customer_id != customer.id:
            raise ValidationError({"invoice": "Invoice does not belong to the selected customer."})

        # Lock the canonical invoice before validating the balance and writing
        # the payment. Never accept an overpayment based on a stale frontend.
        locked_invoice = None
        if invoice:
            locked_invoice = Invoice.objects.select_for_update().get(
                pk=invoice.pk, company=company
            )
            if locked_invoice.kind != "invoice":
                raise ValidationError({"invoice": "Payments require an invoice, not an estimate."})
            if locked_invoice.status == "Cancelled":
                raise ValidationError({"invoice": "Cannot pay a cancelled invoice."})
            balance_due = max(Decimal("0"), locked_invoice.total - locked_invoice.paid)
            if amount > balance_due:
                raise ValidationError({"amount": "Payment exceeds the outstanding invoice balance."})

        payment = serializer.save(
            company=company,
            branch=self.request.user.branch,
            recorded_by=self.request.user,
        )
        if locked_invoice:
            locked_invoice.paid += payment.amount
            locked_invoice.balance = max(
                Decimal("0"), locked_invoice.total - locked_invoice.paid
            )
            if locked_invoice.balance == 0:
                locked_invoice.status = "Paid"
            locked_invoice.save(
                update_fields=["paid", "balance", "status", "updated_at"]
            )
