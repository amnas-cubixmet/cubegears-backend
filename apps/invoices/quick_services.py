from decimal import Decimal

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.accounts.permissions import user_has_permission
from apps.companies.models import Company
from apps.inventory.models import WorkshopFinancialAccount
from apps.payments.serializers import PaymentSerializer
from apps.payments.views import record_payment
from common.viewsets import CompanyScopedModelViewSet
from .calculations import calculate_invoice_amounts, recalculate_invoice, serialize_amounts
from .issuance import finalize_invoice
from .models import QuickService
from .numbering import next_document_number
from .serializers import InvoiceSerializer


class QuickServiceSerializer(serializers.ModelSerializer):
    requestId = serializers.UUIDField(source="start_key", write_only=True)
    invoiceId = serializers.UUIDField(source="invoice.pk", read_only=True, default=None)
    invoiceNumber = serializers.CharField(source="invoice.number", read_only=True, default="")
    invoiceTotal = serializers.DecimalField(
        source="invoice.total", max_digits=12, decimal_places=2, read_only=True, default=None,
    )
    invoiceStatus = serializers.CharField(source="invoice.status", read_only=True, default="")
    closedAt = serializers.DateTimeField(source="invoice.finalized_at", read_only=True, default=None)
    paymentMethod = serializers.CharField(source="invoice.payment_mode", read_only=True, default="")

    class Meta:
        model = QuickService
        fields = (
            "id", "description", "requestId", "created_at", "closedAt",
            "invoiceId", "invoiceNumber", "invoiceTotal", "invoiceStatus", "paymentMethod",
        )

    def get_fields(self):
        fields = super().get_fields()
        if not self.context.get("can_view_invoice"):
            for name in ("invoiceId", "invoiceNumber", "invoiceTotal", "invoiceStatus", "paymentMethod"):
                fields.pop(name)
        return fields


class ServiceLineSerializer(serializers.Serializer):
    description = serializers.CharField(max_length=255)
    qty = serializers.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal("0.01"))
    rate = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    hsnCode = serializers.CharField(max_length=30, required=False, allow_blank=True)
    taxRate = serializers.DecimalField(
        max_digits=5, decimal_places=2, min_value=Decimal("0"), max_value=Decimal("100"), required=False,
    )


class QuickServiceQuoteSerializer(serializers.Serializer):
    items = ServiceLineSerializer(many=True, allow_empty=False, max_length=20)


class QuickServiceCloseSerializer(QuickServiceQuoteSerializer):
    paymentMethod = serializers.ChoiceField(choices=("Cash", "Bank"))
    account = serializers.UUIDField()
    expectedTotal = serializers.DecimalField(max_digits=12, decimal_places=2)


class QuickServiceViewSet(CompanyScopedModelViewSet):
    http_method_names = ["get", "post", "head", "options"]
    queryset = QuickService.objects.select_related("invoice", "branch")
    serializer_class = QuickServiceSerializer
    close_permissions = ["jobs.edit", "invoices.view", "invoices.create", "invoices.edit", "payments.view", "payments.create"]
    action_permission_map = {
        "list": "jobs.view",
        "retrieve": "jobs.view",
        "create": "jobs.create",
        "preview": close_permissions,
        "close": close_permissions,
    }

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["can_view_invoice"] = user_has_permission(self.request.user, "invoices.view")
        return context

    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        service, created = QuickService.objects.get_or_create(
            company=request.user.company, start_key=data["start_key"],
            defaults={"branch": request.user.branch, "description": data["description"], "started_by": request.user},
        )
        if not created and (service.started_by_id != request.user.pk or service.description != data["description"]):
            raise ValidationError({"requestId": "This request belongs to another Quick Service."})
        return Response(self.get_serializer(service).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @staticmethod
    def quote(service, items):
        company = service.company
        gstin = (company.gstin or "").strip()
        invoice_type = "gst" if gstin else "regular"
        # This counter workflow is for services performed at the workshop.
        # For a named recipient or a different place of supply use normal billing.
        recipient = {"placeOfSupply": gstin[:2]} if gstin else {}
        lines = []
        for index, item in enumerate(items, 1):
            if gstin and (not item.get("hsnCode") or "taxRate" not in item):
                raise ValidationError({"items": f"Line {index}: enter the applicable SAC and GST percentage."})
            lines.append({
                **item, "type": "Labour", "unit": "NOS",
                "taxRate": item["taxRate"] if gstin else Decimal("0"),
            })
        amounts = calculate_invoice_amounts(
            company=company, branch=service.branch, invoice_type=invoice_type,
            place_of_supply=recipient.get("placeOfSupply", ""), items=lines,
        )
        if gstin and amounts["taxable"] >= Decimal("50000"):
            raise ValidationError({"items": "Use a standard invoice with recipient details for GST supplies of ₹50,000 or more."})
        if not Decimal("0") < amounts["total"] <= Decimal("9999999999.99"):
            raise ValidationError({"items": "Enter a valid positive service total within the invoice limit."})
        return invoice_type, recipient, lines, amounts

    @action(detail=True, methods=["post"])
    def preview(self, request, pk=None):
        service = self.get_object()
        if hasattr(service, "invoice"):
            raise ValidationError({"detail": "This Quick Service is already closed."})
        payload = QuickServiceQuoteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        _, _, _, amounts = self.quote(service, payload.validated_data["items"])
        return Response(serialize_amounts(amounts))

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def close(self, request, pk=None):
        user = request.user
        # The invoice sequence and payment endpoint use the company lock first.
        company = Company.objects.select_for_update().get(pk=user.company_id)
        service = get_object_or_404(QuickService.objects.select_for_update(), company=company, pk=self.get_object().pk)
        if hasattr(service, "invoice"):
            return Response(self.get_serializer(service).data)

        payload = QuickServiceCloseSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        invoice_type, recipient, lines, amounts = self.quote(service, data["items"])
        if data["expectedTotal"] != amounts["total"]:
            raise ValidationError({"expectedTotal": "The invoice total changed. Review the updated total before collecting payment."})
        account = WorkshopFinancialAccount.objects.filter(pk=data["account"], company=company, is_active=True).first()
        if not account:
            raise ValidationError({"account": "Select an active account owned by your workshop."})

        doc = InvoiceSerializer(data={
            "date": timezone.localdate(), "invoiceType": invoice_type, "customer": recipient,
            "items": lines, "paymentMode": data["paymentMethod"], "paymentType": data["paymentMethod"],
        }, context={"request": request})
        doc.is_valid(raise_exception=True)
        invoice = doc.save(
            company=company, branch=service.branch, quick_service=service,
            number=next_document_number(company, "invoice"),
        )
        recalculate_invoice(invoice)
        finalize_invoice(invoice, user)

        receipt = PaymentSerializer(data={
            "invoice": invoice.pk, "date": invoice.date, "amount": invoice.total,
            "method": data["paymentMethod"], "account": account.pk, "reference": invoice.number,
        })
        receipt.is_valid(raise_exception=True)
        record_payment(receipt, user, branch=service.branch)
        invoice.refresh_from_db()
        return Response(self.get_serializer(service).data)
