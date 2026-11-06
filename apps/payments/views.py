from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from rest_framework import decorators, response, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError

from common.viewsets import CompanyScopedModelViewSet
from apps.invoices.models import Invoice
from apps.jobs.audit import record_job_event
from .models import Payment
from .serializers import PaymentSerializer


class PaymentViewSet(CompanyScopedModelViewSet):
    queryset = Payment.objects.select_related("customer", "invoice", "recorded_by").all()
    serializer_class = PaymentSerializer
    action_permission_map = {"reverse": "payments.reverse"}

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
        if serializer.validated_data.get("status", "Completed") != "Completed":
            raise ValidationError({"status": "New payments must be completed. Record only confirmed collections."})

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
            status="Completed",
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
            if getattr(locked_invoice, 'job_id', None):
                record_job_event(locked_invoice.job, self.request.user, 'Payment Received',
                                 f'Payment registered against {locked_invoice.number}',
                                 {'paymentId': str(payment.pk), 'invoiceId': str(locked_invoice.pk)})

    def update(self, request, *args, **kwargs):
        # Mutating posted financial rows would desync Invoice.paid and the ledger.
        raise PermissionDenied("Posted payments are immutable. Use the audited reversal endpoint.")

    def partial_update(self, request, *args, **kwargs):
        raise PermissionDenied("Posted payments are immutable. Use the audited reversal endpoint.")

    def destroy(self, request, *args, **kwargs):
        raise PermissionDenied("Posted payments cannot be deleted. Use the audited reversal endpoint.")

    @decorators.action(detail=True, methods=["post"], url_path="reverse")
    @transaction.atomic
    def reverse(self, request, pk=None):
        reason = str(request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError({"reason": "An audit reason is required to reverse a payment."})
        if len(reason) > 1500:
            raise ValidationError({"reason": "Reason must not exceed 1500 characters."})
        payment = Payment.objects.select_for_update().filter(
            company=request.user.company, pk=pk
        ).first()
        if payment is None:
            return response.Response({"detail": "Payment not found."}, status=404)
        if payment.reversed_at:
            return response.Response(self.get_serializer(payment).data)
        if payment.status != "Completed":
            raise ValidationError({"status": "Only completed payments can be reversed."})

        if payment.invoice_id:
            invoice = Invoice.objects.select_for_update().filter(
                company=request.user.company, pk=payment.invoice_id
            ).first()
            if not invoice:
                raise ValidationError({"invoice": "Invoice is no longer available for reconciliation."})
            if invoice.paid < payment.amount:
                raise ValidationError(
                    {"invoice": "Invoice ledger does not match the posted payment. Reconcile before reversal."}
                )
            invoice.paid -= payment.amount
            invoice.balance = max(Decimal("0"), invoice.total - invoice.paid)
            if invoice.status == "Paid" and invoice.balance > 0:
                invoice.status = "Finalized"
            invoice.save(update_fields=["paid", "balance", "status", "updated_at"])

        payment.status = "Reversed"
        payment.reversed_at = timezone.now()
        payment.reversed_by = request.user
        payment.reversal_reason = reason
        payment.save(update_fields=[
            "status", "reversed_at", "reversed_by", "reversal_reason", "updated_at"
        ])
        if payment.invoice_id and payment.invoice.job_id:
            record_job_event(
                payment.invoice.job, request.user, "Payment Reversed",
                f"Customer payment reversed against invoice {payment.invoice.number}",
                {"paymentId": str(payment.pk), "invoiceId": str(payment.invoice_id), "reason": reason},
            )
        return response.Response(self.get_serializer(payment).data, status=status.HTTP_200_OK)
