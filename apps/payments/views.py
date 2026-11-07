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
from .models import Payment, CustomerCreditAllocation
from .customer_credit import available_advance, paid_status
from .serializers import PaymentSerializer


class PaymentViewSet(CompanyScopedModelViewSet):
    queryset = Payment.objects.select_related("customer", "invoice", "recorded_by").all()
    serializer_class = PaymentSerializer
    action_permission_map = {
        "reverse": "payments.reverse",
        "apply_advance": "payments.create",
        "reverse_advance_application": "payments.reverse",
        "refund_advance": "payments.reverse",
        "advances": "payments.view",
    }

    def get_queryset(self):
        qs = super().get_queryset()
        invoice_id = self.request.query_params.get("invoice")
        if invoice_id:
            try:
                UUID(str(invoice_id))
            except (ValueError, TypeError, AttributeError):
                return qs.none()
            qs = qs.filter(invoice_id=invoice_id)
        customer_id = self.request.query_params.get("customer")
        if customer_id:
            try:
                UUID(str(customer_id))
            except (ValueError, TypeError, AttributeError):
                return qs.none()
            qs = qs.filter(customer_id=customer_id)
        return qs

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """A repeated request key returns the original receipt, never posts twice."""
        from apps.companies.models import Company

        key = str(request.data.get("idempotency_key") or "").strip()
        if key:
            # All company write requests with a key serialize on this lock.
            Company.objects.select_for_update().get(pk=request.user.company_id)
            existing = Payment.objects.filter(
                company_id=request.user.company_id, idempotency_key=key,
            ).first()
            if existing:
                from apps.finance.views import valid_money
                invoice = str(request.data.get("invoice") or "")
                customer = str(request.data.get("customer") or "")
                # An invoice-only receipt can derive its customer server-side.
                if not customer and invoice == str(existing.invoice_id or ""):
                    customer = str(existing.customer_id or "")
                account = str(request.data.get("account") or "")
                amount = valid_money(request.data.get("amount"))
                if (
                    str(existing.customer_id or "") != customer
                    or str(existing.invoice_id or "") != invoice
                    or str(existing.account_id or "") != account
                    or existing.amount != amount
                    or existing.direction != str(request.data.get("direction") or "IN")
                    or existing.method != str(request.data.get("method") or "Cash")
                ):
                    raise ValidationError({"idempotency_key": "Request key belongs to a different payment."})
                return response.Response(self.get_serializer(existing).data, status=status.HTTP_200_OK)
        return super().create(request, *args, **kwargs)

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
        if invoice and customer is None:
            customer = invoice.customer
        if direction == "IN" and not invoice:
            if not customer:
                raise ValidationError({"customer": "Select the customer for this advance receipt."})
            if not serializer.validated_data.get("idempotency_key"):
                raise ValidationError({"idempotency_key": "A unique request key is required for advances."})
        if direction == "OUT" and customer:
            raise ValidationError({
                "customer": "Customer refunds must use the controlled refund-advance workflow."
            })
        account = serializer.validated_data.get("account")
        if not account:
            raise ValidationError({"account": "Select an authorized receiving or paying account."})
        key = serializer.validated_data.get("idempotency_key")
        if key and Payment.objects.filter(company=company, idempotency_key=key).exists():
            raise ValidationError({"idempotency_key": "This request was already processed; no duplicate payment was posted."})
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
            if locked_invoice.status not in {"Finalized", "Paid", "Partially Paid", "Overdue"} or not locked_invoice.finalized_at:
                raise ValidationError({"invoice": "Only finalized, non-cancelled invoices can receive customer payments."})
            balance_due = max(Decimal("0"), locked_invoice.total - locked_invoice.paid)
            if amount > balance_due:
                raise ValidationError({"amount": "Payment exceeds the outstanding invoice balance."})

        payment = serializer.save(
            status="Completed",
            customer=customer,
            company=company,
            branch=self.request.user.branch,
            recorded_by=self.request.user,
        )
        if locked_invoice:
            locked_invoice.paid += payment.amount
            locked_invoice.balance = max(
                Decimal("0"), locked_invoice.total - locked_invoice.paid
            )
            locked_invoice.status = paid_status(locked_invoice)
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
        if payment.invoice_id is None and payment.direction == "IN" and payment.customer_id:
            active_credit = payment.credit_allocations.filter(reversed_at__isnull=True).exists()
            if active_credit:
                raise ValidationError({
                    "payment": "This advance has been applied or refunded. Reverse its allocations first."
                })
        refund_allocation = CustomerCreditAllocation.objects.select_for_update().filter(
            company=request.user.company, refund_payment=payment,
            reversed_at__isnull=True,
        ).first()

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
            invoice.status = paid_status(invoice)
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

        if refund_allocation:
            refund_allocation.reversed_at = timezone.now()
            refund_allocation.reversed_by = request.user
            refund_allocation.reversal_reason = reason
            refund_allocation.save(update_fields=[
                "reversed_at", "reversed_by", "reversal_reason", "updated_at",
            ])
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


    def _credit_request_key(self, data):
        key = str(data.get("idempotency_key") or "").strip()
        if not key or len(key) > 120:
            raise ValidationError({
                "idempotency_key": "Provide a unique request key (1–120 characters).",
            })
        return key

    def _locked_advance(self, request, pk):
        advance = Payment.objects.select_for_update().filter(
            company_id=request.user.company_id, pk=pk,
        ).first()
        if not advance:
            raise ValidationError({"advance": "Customer advance was not found."})
        if advance.direction != "IN" or advance.invoice_id or not advance.customer_id:
            raise ValidationError({"advance": "Choose a real unapplied customer advance receipt."})
        if advance.status != "Completed" or advance.reversed_at:
            raise ValidationError({"advance": "Reversed advances cannot be adjusted or refunded."})
        return advance

    @decorators.action(detail=False, methods=["get"], url_path="advances")
    def advances(self, request):
        from apps.customers.models import Customer

        customer_id = request.query_params.get("customerId") or ""
        try:
            UUID(str(customer_id))
        except (ValueError, TypeError, AttributeError):
            raise ValidationError({"customerId": "Select a valid customer."})
        customer = Customer.objects.filter(
            company_id=request.user.company_id, pk=customer_id,
        ).first()
        if customer is None:
            raise ValidationError({"customerId": "Customer was not found in this workshop."})
        rows = Payment.objects.filter(
            company_id=request.user.company_id, customer=customer,
            invoice__isnull=True, direction="IN", status="Completed",
            reversed_at__isnull=True,
        ).order_by("date", "created_at")
        return response.Response([{
            "id": str(payment.pk), "customerId": str(customer.pk),
            "date": payment.date.isoformat(),
            "amount": str(payment.amount),
            "available": str(available_advance(payment)),
            "method": payment.method, "reference": payment.reference,
        } for payment in rows])

    @transaction.atomic
    @decorators.action(detail=True, methods=["post"], url_path="apply-advance")
    def apply_advance(self, request, pk=None):
        """Settle a finalized invoice from prepaid funds, with NO new bank posting."""
        from apps.finance.views import valid_money

        advance = self._locked_advance(request, pk)
        key = self._credit_request_key(request.data)
        invoice_id = request.data.get("invoiceId")
        try:
            UUID(str(invoice_id))
        except (ValueError, TypeError, AttributeError):
            raise ValidationError({"invoiceId": "Select a valid finalized customer invoice."})
        amount = valid_money(request.data.get("amount"))
        existing = CustomerCreditAllocation.objects.filter(
            company_id=request.user.company_id, idempotency_key=key,
        ).first()
        if existing:
            if existing.advance_payment_id != advance.pk or existing.invoice_id != UUID(str(invoice_id)) or existing.amount != amount:
                raise ValidationError({"idempotency_key": "Request key belongs to a different credit transaction."})
            return response.Response({
                "applicationId": str(existing.pk),
                "advanceId": str(advance.pk),
                "invoiceId": str(existing.invoice_id),
                "appliedAmount": str(existing.amount),
                "reversed": bool(existing.reversed_at),
                "advanceAvailable": str(available_advance(advance)),
            }, status=status.HTTP_200_OK)
        invoice = Invoice.objects.select_for_update().filter(
            company_id=request.user.company_id, pk=invoice_id,
            customer_id=advance.customer_id, kind="invoice",
            finalized_at__isnull=False,
        ).exclude(status__in=["Draft", "Cancelled"]).first()
        if not invoice:
            raise ValidationError({"invoiceId": "Select a finalized invoice for the same customer."})
        if amount > available_advance(advance):
            raise ValidationError({"amount": "Credit amount exceeds the customer's available advance."})
        due = max(Decimal("0.00"), invoice.total - invoice.paid)
        if amount > due:
            raise ValidationError({"amount": "Credit amount exceeds the invoice's outstanding balance."})
        application = CustomerCreditAllocation.objects.create(
            company_id=request.user.company_id, branch=invoice.branch,
            advance_payment=advance, invoice=invoice, amount=amount,
            idempotency_key=key, created_by=request.user,
        )
        invoice.paid += amount
        invoice.balance = max(Decimal("0.00"), invoice.total - invoice.paid)
        invoice.status = paid_status(invoice)
        invoice.save(update_fields=["paid", "balance", "status", "updated_at"])
        if invoice.job_id:
            record_job_event(
                invoice.job, request.user, "Customer Credit Applied",
                f"Advance applied to {invoice.number}", {
                    "applicationId": str(application.pk), "advanceId": str(advance.pk),
                    "amount": str(amount),
                },
            )
        return response.Response({
            "applicationId": str(application.pk),
            "advanceId": str(advance.pk),
            "invoiceId": str(invoice.pk),
            "appliedAmount": str(amount),
            "invoiceBalance": str(invoice.balance),
            "invoiceStatus": invoice.status,
            "advanceAvailable": str(available_advance(advance)),
        }, status=status.HTTP_201_CREATED)

    @transaction.atomic
    @decorators.action(detail=True, methods=["post"], url_path="reverse-advance-application")
    def reverse_advance_application(self, request, pk=None):
        """Audited reversal of a credit application; no cash/bank movement."""
        advance = self._locked_advance(request, pk)
        reason = str(request.data.get("reason") or "").strip()
        if len(reason) < 5 or len(reason) > 1000:
            raise ValidationError({"reason": "Provide a reason (5–1000 characters)."})
        allocation_id = request.data.get("applicationId")
        try:
            UUID(str(allocation_id))
        except (ValueError, TypeError, AttributeError):
            raise ValidationError({"applicationId": "Select a valid credit allocation."})
        allocation = CustomerCreditAllocation.objects.select_for_update().filter(
            company_id=request.user.company_id, pk=allocation_id,
            advance_payment=advance, invoice__isnull=False,
        ).first()
        if not allocation:
            raise ValidationError({"applicationId": "Credit application was not found."})
        if allocation.reversed_at:
            return response.Response({"applicationId": str(allocation.pk), "status": "Reversed"})
        invoice = Invoice.objects.select_for_update().get(
            pk=allocation.invoice_id, company_id=request.user.company_id,
        )
        if invoice.paid < allocation.amount:
            raise ValidationError({
                "invoice": "Invoice paid amount is below this credit application. Reconcile first.",
            })
        invoice.paid -= allocation.amount
        invoice.balance = max(Decimal("0.00"), invoice.total - invoice.paid)
        invoice.status = paid_status(invoice)
        invoice.save(update_fields=["paid", "balance", "status", "updated_at"])
        allocation.reversed_at = timezone.now()
        allocation.reversed_by = request.user
        allocation.reversal_reason = reason
        allocation.save(update_fields=[
            "reversed_at", "reversed_by", "reversal_reason", "updated_at",
        ])
        if invoice.job_id:
            record_job_event(
                invoice.job, request.user, "Customer Credit Reversed",
                f"Credit application reversed on {invoice.number}", {
                    "applicationId": str(allocation.pk), "reason": reason,
                },
            )
        return response.Response({
            "applicationId": str(allocation.pk), "status": "Reversed",
            "invoiceBalance": str(invoice.balance),
            "advanceAvailable": str(available_advance(advance)),
        })

    @transaction.atomic
    @decorators.action(detail=True, methods=["post"], url_path="refund-advance")
    def refund_advance(self, request, pk=None):
        """Refund only unspent credit; debit the real account exactly once."""
        from apps.finance.views import valid_money, account_balance
        from apps.inventory.models import WorkshopFinancialAccount, WorkshopAccountEntry

        advance = self._locked_advance(request, pk)
        key = self._credit_request_key(request.data)
        amount = valid_money(request.data.get("amount"))
        reason = str(request.data.get("reason") or "").strip()
        if len(reason) < 5 or len(reason) > 1000:
            raise ValidationError({"reason": "Provide a refund reason (5–1000 characters)."})
        existing = CustomerCreditAllocation.objects.select_related(
            "refund_payment",
        ).filter(company_id=request.user.company_id, idempotency_key=key).first()
        if existing:
            if existing.advance_payment_id != advance.pk or not existing.refund_payment_id or existing.amount != amount:
                raise ValidationError({"idempotency_key": "Request key belongs to another credit transaction."})
            return response.Response({
                "refundId": str(existing.refund_payment_id),
                "applicationId": str(existing.pk),
                "reversed": bool(existing.reversed_at),
                "advanceAvailable": str(available_advance(advance)),
            })
        if amount > available_advance(advance):
            raise ValidationError({"amount": "Refund exceeds unapplied customer advance."})
        account_id = request.data.get("accountId") or advance.account_id
        account = WorkshopFinancialAccount.objects.select_for_update().filter(
            company_id=request.user.company_id, pk=account_id, is_active=True,
        ).first()
        if not account:
            raise ValidationError({"accountId": "Select an active workshop cash/bank account."})
        if account_balance(account) < amount:
            raise ValidationError({"amount": "Insufficient funds to refund the customer from this account."})
        refund = Payment.objects.create(
            company_id=request.user.company_id, branch=request.user.branch,
            customer=advance.customer, account=account, invoice=None,
            amount=amount, date=timezone.localdate(), direction="OUT",
            method=str(request.data.get("method") or advance.method)[:50],
            reference=str(request.data.get("reference") or "")[:120],
            category="Customer Advance Refund", notes=reason,
            status="Completed", recorded_by=request.user,
            idempotency_key=f"rf-{key}",
        )
        WorkshopAccountEntry.objects.create(
            company_id=request.user.company_id, branch=request.user.branch,
            account=account, amount=-amount,
            entry_type="outgoing_payment",
            source_key=f"customer_payment_{refund.pk}",
            reference=f"Refund {refund.pk}", posted_at=timezone.now(),
            created_by=request.user,
        )
        allocation = CustomerCreditAllocation.objects.create(
            company_id=request.user.company_id, branch=request.user.branch,
            advance_payment=advance, refund_payment=refund, amount=amount,
            idempotency_key=key, created_by=request.user,
        )
        return response.Response({
            "refundId": str(refund.pk),
            "applicationId": str(allocation.pk),
            "refundedAmount": str(amount),
            "advanceAvailable": str(available_advance(advance)),
        }, status=status.HTTP_201_CREATED)
