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

        if amount is None or not amount.is_finite() or amount <= Decimal("0"):
            raise ValidationError({"amount": "Payment must be greater than zero."})
        direction = serializer.validated_data.get("direction", "IN")
        if direction not in ("IN", "OUT"):
            raise ValidationError({"direction": "Choose IN or OUT."})
        if direction == "OUT":
            category = str(serializer.validated_data.get("category") or "").strip().casefold()
            restricted = ("supplier", "spare part", "stock purchase", "inventory purchase", "salary", "wage", "overtime")
            if any(label in category for label in restricted):
                raise ValidationError({
                    "category": "Use the linked Supplier Payments or Payroll workflow so outstanding liabilities and bank ledger stay synchronized."
                })
        if invoice and direction != "IN":
            raise ValidationError({"direction": "Invoice collections must be incoming payments."})
        account = serializer.validated_data.get("account")
        if direction == "OUT" and not account:
            raise ValidationError({"account": "Select an account for outgoing payments."})
        key = serializer.validated_data.get("idempotency_key")
        if key and Payment.objects.filter(company=company, idempotency_key=key).exists():
            raise ValidationError({"idempotency_key": "This payment has already been recorded."})
        if account:
            from apps.inventory.models import WorkshopFinancialAccount
            from apps.finance.views import account_balance
            account = WorkshopFinancialAccount.objects.select_for_update().filter(
                pk=account.pk, company=company, is_active=True
            ).first()
            if not account:
                raise ValidationError({"account": "Select an active account owned by your company."})
            if direction == "OUT" and account_balance(account) < amount:
                raise ValidationError({"amount": "Insufficient funds in the selected account."})

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

        # Post to workshop account ledger if an account is linked
        if payment.account_id:
            from apps.inventory.models import WorkshopAccountEntry
            # IN = money received = positive; OUT = money paid = negative
            signed_amount = payment.amount if payment.direction == "IN" else -payment.amount
            source_key = f"customer_payment_{payment.pk}"
            WorkshopAccountEntry.objects.create(
                company=company,
                branch=self.request.user.branch,
                account=payment.account,
                amount=signed_amount,
                entry_type="customer_payment" if payment.direction == "IN" else "outgoing_payment",
                source_key=source_key,
                reference=f"Payment {payment.pk}" + (f" | {payment.reference}" if payment.reference else ""),
                posted_at=timezone.now(),
                created_by=self.request.user,
            )

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

        # Reverse the original financial posting, not an unrelated legacy balance.
        original_entry = None
        if payment.account_id:
            from apps.inventory.models import WorkshopAccountEntry
            from apps.finance.views import account_balance
            original_entry = WorkshopAccountEntry.objects.select_related("account").filter(
                company=request.user.company,
                source_key=f"customer_payment_{payment.pk}",
            ).first()
            if original_entry and original_entry.amount > 0:
                if account_balance(original_entry.account) < original_entry.amount:
                    raise ValidationError({
                        "account": "Insufficient funds for reversal. Reconcile the account first."
                    })

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

        if original_entry:
            WorkshopAccountEntry.objects.create(
                company=request.user.company,
                branch=request.user.branch,
                account=original_entry.account,
                amount=-original_entry.amount,
                entry_type="payment_reversal",
                source_key=f"customer_payment_reverse_{payment.pk}",
                reference=reason[:120],
                posted_at=timezone.now(),
                created_by=request.user,
            )

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
