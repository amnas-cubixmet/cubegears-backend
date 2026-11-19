import json
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.accounts.permissions import user_has_permission
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.vehicles.models import Vehicle
from apps.services.models import Service
from apps.inventory.models import StockItem, WorkshopFinancialAccount
from apps.inventory.branch_stock import branch_stock_snapshot
from apps.jobs.models import OutsideLabourCharge
from apps.jobs.india_gst import state_code
from apps.payments.serializers import PaymentSerializer
from apps.payments.views import record_payment
from common.viewsets import CompanyScopedModelViewSet
from .calculations import calculate_invoice_amounts, serialize_amounts, recalculate_invoice
from .issuance import finalize_invoice
from .models import QuickService
from .numbering import next_document_number
from .serializers import InvoiceSerializer


class QuickServiceSerializer(serializers.ModelSerializer):
    invoiceId = serializers.UUIDField(source="invoice.pk", read_only=True, default=None)
    invoiceNumber = serializers.CharField(source="invoice.number", read_only=True, default="")
    invoiceTotal = serializers.DecimalField(
        source="invoice.total", max_digits=12, decimal_places=2, read_only=True, default=None,
    )
    invoiceStatus = serializers.CharField(source="invoice.status", read_only=True, default="")
    closedAt = serializers.DateTimeField(source="invoice.finalized_at", read_only=True, default=None)
    paymentMethod = serializers.CharField(source="invoice.payment_mode", read_only=True, default="")
    customerId = serializers.UUIDField(source="customer_id", read_only=True, allow_null=True)
    customerName = serializers.CharField(source="customer.name", read_only=True, default="")
    vehicleId = serializers.UUIDField(source="vehicle_id", read_only=True, allow_null=True)
    vehicleReg = serializers.CharField(source="vehicle.registration", read_only=True, default="")
    items = serializers.JSONField(read_only=True)
    notes = serializers.CharField(read_only=True)

    class Meta:
        model = QuickService
        fields = (
            "id", "description", "created_at", "closedAt",
            "invoiceId", "invoiceNumber", "invoiceTotal", "invoiceStatus", "paymentMethod",
            "customerId", "customerName", "vehicleId", "vehicleReg", "items", "notes",
        )

    def get_fields(self):
        fields = super().get_fields()
        if not self.context.get("can_view_invoice"):
            for name in ("invoiceId", "invoiceNumber", "invoiceTotal", "invoiceStatus", "paymentMethod"):
                fields.pop(name)
        if not self.context.get("can_view_customer"):
            for name in ("customerId", "customerName", "vehicleId", "vehicleReg"):
                fields.pop(name)
        return fields


class QuickServiceStartSerializer(serializers.Serializer):
    requestId = serializers.UUIDField()
    description = serializers.CharField(max_length=255, required=False, allow_blank=True)
    customerId = serializers.UUIDField(required=False, allow_null=True)
    vehicleId = serializers.UUIDField(required=False, allow_null=True)
    items = serializers.ListField(child=serializers.DictField(), required=False, allow_empty=True, max_length=100)
    notes = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class ServiceLineSerializer(serializers.Serializer):
    description = serializers.CharField(max_length=255)
    qty = serializers.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal("0.01"))
    rate = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    hsnCode = serializers.CharField(max_length=30, required=False, allow_blank=True)
    taxRate = serializers.DecimalField(
        max_digits=5, decimal_places=2, min_value=Decimal("0"), max_value=Decimal("100"), required=False,
    )


class QuickServiceQuoteSerializer(serializers.Serializer):
    items = ServiceLineSerializer(many=True, allow_empty=False, max_length=20, required=False)


class QuickServiceCloseSerializer(QuickServiceQuoteSerializer):
    paymentMethod = serializers.ChoiceField(choices=("Cash", "Bank"))
    account = serializers.UUIDField()
    expectedTotal = serializers.DecimalField(max_digits=12, decimal_places=2)


class QuickServiceViewSet(CompanyScopedModelViewSet):
    http_method_names = ["get", "post", "head", "options"]
    queryset = QuickService.objects.select_related(
        "invoice", "branch", "customer", "vehicle",
    ).prefetch_related("outside_labour")
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
        context["can_view_customer"] = user_has_permission(self.request.user, "customers.view")
        return context

    @staticmethod
    def _decimal(value, field, *, minimum=Decimal("0.01")):
        try:
            amount = Decimal(str(value))
        except (TypeError, ValueError, InvalidOperation):
            raise ValidationError({field: "Enter a valid amount."})
        if not amount.is_finite() or amount.as_tuple().exponent < -2 or amount < minimum:
            raise ValidationError({field: "Enter a valid amount with at most two decimal places."})
        return amount.quantize(Decimal("0.01"))

    def _normalize_items(self, request, company, branch, raw_items):
        if len(raw_items) > 100:
            raise ValidationError({"items": "Add up to 100 service, part and outside labour lines."})
        gst_registered = bool(str(company.gstin or "").strip())
        result = []
        for index, raw in enumerate(raw_items, 1):
            prefix = f"items.{index - 1}"
            kind = str(raw.get("kind") or (
                "part" if raw.get("inventoryId") else
                "service" if raw.get("serviceId") else
                "outside" if raw.get("workerName") else "custom"
            )).strip().lower()
            qty = self._decimal(raw.get("qty", "1"), f"{prefix}.qty")
            if kind == "service":
                if not user_has_permission(request.user, "services.view"):
                    raise PermissionDenied("Services View permission is required to add catalogue services.")
                service = Service.objects.filter(
                    pk=raw.get("serviceId"), company=company, is_active=True,
                ).first()
                if not service:
                    raise ValidationError({f"{prefix}.serviceId": "Select an active service from this workshop."})
                rate = service.price
                tax_rate = service.tax_rate if service.tax_treatment == "taxable" and gst_registered else Decimal("0")
                hsn = service.hsn_sac.strip()
                if gst_registered and not hsn:
                    raise ValidationError({f"{prefix}.serviceId": f"Add an SAC code to {service.name} in Services before using it on a GST invoice."})
                result.append({
                    "kind": "service", "serviceId": str(service.pk), "type": "Labour",
                    "description": service.name, "qty": str(qty), "rate": str(rate),
                    "hsnCode": hsn, "taxRate": str(tax_rate), "unit": service.unit or "Job",
                })
            elif kind == "part":
                if not user_has_permission(request.user, "stock.view"):
                    raise PermissionDenied("Stock View permission is required to search and add parts.")
                item = StockItem.objects.select_for_update().filter(
                    pk=raw.get("inventoryId"), company=company, status="Active",
                ).first()
                if not item:
                    raise ValidationError({f"{prefix}.inventoryId": "Select an active part from this workshop."})
                snapshot = branch_stock_snapshot(item, branch)
                available = Decimal(str((snapshot or {}).get("branchAvailable", "0")))
                if qty > available:
                    raise ValidationError({f"{prefix}.qty": f"Only {available} available in this branch for {item.name}."})
                hsn = item.hsn_code.strip()
                if gst_registered and not hsn:
                    raise ValidationError({f"{prefix}.inventoryId": f"Add an HSN code to {item.name} in Inventory before invoicing it."})
                result.append({
                    "kind": "part", "type": "Spare Part", "inventoryId": str(item.pk),
                    "description": item.name, "qty": str(qty), "rate": str(item.selling_price),
                    "purchasePrice": str(item.cost_price), "hsnCode": hsn,
                    "taxRate": str(item.tax if gst_registered else Decimal("0")),
                    "unit": item.unit or "Piece",
                })
            elif kind == "outside":
                if not user_has_permission(request.user, "expenses.create"):
                    raise PermissionDenied("Expenses Create permission is required to add outside labour.")
                worker_name = str(raw.get("workerName") or "").strip()
                worker_phone = str(raw.get("workerPhone") or "").strip()
                work_description = str(raw.get("workDescription") or raw.get("description") or "").strip()
                if not worker_name or len(worker_name) > 160 or not work_description or len(work_description) > 300:
                    raise ValidationError({prefix: "Enter a valid outside worker name and work description."})
                worker_charge = self._decimal(raw.get("workerCharge"), f"{prefix}.workerCharge")
                customer_charge = self._decimal(raw.get("customerCharge", raw.get("rate")), f"{prefix}.customerCharge")
                hsn = str(raw.get("hsnCode") or "").strip()
                if gst_registered and (not hsn or raw.get("taxRate") in (None, "")):
                    raise ValidationError({prefix: "Enter the applicable SAC and GST percentage for outside labour."})
                tax_rate = self._decimal(raw.get("taxRate", "0"), f"{prefix}.taxRate", minimum=Decimal("0")) if gst_registered else Decimal("0")
                if tax_rate > 100:
                    raise ValidationError({f"{prefix}.taxRate": "GST percentage cannot exceed 100."})
                result.append({
                    "kind": "outside", "type": "Outside Labour",
                    "description": f"{work_description} — {worker_name}",
                    "qty": "1.00", "rate": str(customer_charge),
                    "customerCharge": str(customer_charge), "workerCharge": str(worker_charge),
                    "workerName": worker_name, "workerPhone": worker_phone,
                    "workDescription": work_description, "hsnCode": hsn,
                    "taxRate": str(tax_rate), "unit": "Job",
                })
            else:
                description = str(raw.get("description") or "").strip()
                if not description or len(description) > 255:
                    raise ValidationError({f"{prefix}.description": "Enter a service description (maximum 255 characters)."})
                rate = self._decimal(raw.get("rate"), f"{prefix}.rate")
                hsn = str(raw.get("hsnCode") or "").strip()
                if gst_registered and (not hsn or raw.get("taxRate") in (None, "")):
                    raise ValidationError({prefix: "Enter the applicable SAC and GST percentage for this custom line."})
                tax_rate = self._decimal(raw.get("taxRate", "0"), f"{prefix}.taxRate", minimum=Decimal("0")) if gst_registered else Decimal("0")
                if tax_rate > 100:
                    raise ValidationError({f"{prefix}.taxRate": "GST percentage cannot exceed 100."})
                result.append({
                    "kind": "custom", "type": str(raw.get("type") or "Labour")[:30],
                    "description": description, "qty": str(qty), "rate": str(rate),
                    "hsnCode": hsn, "taxRate": str(tax_rate),
                    "unit": str(raw.get("unit") or "Job")[:20],
                })
        return result

    @staticmethod
    def _customer_snapshot(service, place_of_supply):
        customer = service.customer
        if not customer:
            return {"placeOfSupply": place_of_supply} if place_of_supply else {}
        return {
            "name": customer.name, "phone": customer.phone, "email": customer.email,
            "address": customer.address, "state": customer.state, "gstin": customer.gstin,
            "placeOfSupply": place_of_supply,
        }

    @staticmethod
    def _vehicle_snapshot(service):
        vehicle = service.vehicle
        if not vehicle:
            return {}
        return {
            "registration": vehicle.registration,
            "makeModel": " ".join(filter(None, [vehicle.make, vehicle.model, vehicle.variant])),
            "vin": vehicle.vin or "",
        }

    @transaction.atomic
    def create(self, request):
        payload = QuickServiceStartSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        company = Company.objects.select_for_update().get(pk=request.user.company_id)
        branch = request.user.branch
        if not branch or branch.company_id != company.pk or not branch.is_active:
            raise ValidationError({"branch": "Select an active workshop branch before starting Quick Service."})

        customer = None
        if data.get("customerId"):
            if not user_has_permission(request.user, "customers.view"):
                raise PermissionDenied("Customers View permission is required to select a customer.")
            customer = Customer.objects.filter(pk=data["customerId"], company=company).first()
            if not customer or customer.status == "archived":
                raise ValidationError({"customerId": "Select an active customer from this workshop."})

        vehicle = None
        if data.get("vehicleId"):
            if not user_has_permission(request.user, "vehicles.view"):
                raise PermissionDenied("Vehicles View permission is required to select a vehicle.")
            vehicle = Vehicle.objects.select_related("customer").filter(
                pk=data["vehicleId"], company=company,
            ).first()
            if not vehicle or str(vehicle.status).lower() == "archived":
                raise ValidationError({"vehicleId": "Select an active vehicle from this workshop."})
            if customer and vehicle.customer_id != customer.pk:
                raise ValidationError({"vehicleId": "The selected vehicle belongs to a different customer."})
            if not customer:
                customer = vehicle.customer

        requested_description = str(data.get("description") or "").strip()
        notes = str(data.get("notes") or "").strip()
        fingerprint_payload = {
            "description": requested_description,
            "customer": str(customer.pk) if customer else "",
            "vehicle": str(vehicle.pk) if vehicle else "",
            "items": data.get("items", []),
            "notes": notes,
        }
        fingerprint = sha256(json.dumps(
            fingerprint_payload, sort_keys=True, separators=(",", ":"), default=str,
        ).encode()).hexdigest()
        existing = QuickService.objects.select_for_update().filter(
            company=company, start_key=data["requestId"],
        ).first()
        if existing:
            if existing.start_fingerprint and existing.start_fingerprint != fingerprint:
                raise ValidationError({"requestId": "This request key was already used for different Quick Service details."})
            if not existing.start_fingerprint and requested_description and existing.description != requested_description:
                raise ValidationError({"requestId": "This request key belongs to another Quick Service."})
            return Response(self.get_serializer(existing).data, status=status.HTTP_200_OK)

        normalized = self._normalize_items(request, company, branch, data.get("items", []))
        if not normalized:
            raise ValidationError({"items": "Add at least one service, part, custom item or outside labour before starting."})
        description = requested_description or (
            ", ".join(row["description"] for row in normalized[:2])[:255] or "Quick Service"
        )

        service = QuickService.objects.create(
            company=company, branch=branch, description=description,
            start_key=data["requestId"], start_fingerprint=fingerprint,
            started_by=request.user, customer=customer, vehicle=vehicle,
            items=normalized, notes=notes,
        )
        # Outside worker costs remain pending liabilities. The customer charge
        # is included in the invoice line; paying the worker later creates one
        # linked Expense through the existing Outside Labour payment workflow.
        for line in service.items:
            if line.get("kind") != "outside":
                continue
            record = OutsideLabourCharge.objects.create(
                company=company, branch=branch, job=None, quick_service=service,
                worker_name=line["workerName"], worker_phone=line.get("workerPhone", ""),
                work_description=line["workDescription"],
                customer_charge=Decimal(line["customerCharge"]),
                worker_charge=Decimal(line["workerCharge"]),
                created_by=request.user,
            )
            line["outsideLabourId"] = str(record.pk)
        service.save(update_fields=["items", "updated_at"])
        return Response(self.get_serializer(service).data, status=status.HTTP_201_CREATED)

    @staticmethod
    def quote(service, items):
        company = service.company
        gstin = (company.gstin or "").strip()
        invoice_type = "gst" if gstin else "regular"
        place = (
            service.customer.state if service.customer and service.customer.state else
            service.branch.state if service.branch and service.branch.state else company.state
        )
        place_code = state_code(place) or (gstin[:2] if len(gstin) == 15 else "")
        recipient = {"placeOfSupply": place_code} if place_code else {}
        lines = []
        for index, item in enumerate(items, 1):
            kind = item.get("kind", "")
            if kind == "outside":
                line = {
                    "type": "Outside Labour",
                    "description": item.get("description") or f'{item.get("workDescription", "Outside work")} — {item.get("workerName", "Worker")}',
                    "qty": "1.00", "rate": item.get("customerCharge") or item.get("rate"),
                    "hsnCode": item.get("hsnCode", ""), "taxRate": item.get("taxRate", "0"),
                    "unit": "Job",
                }
            else:
                line = {
                    "type": item.get("type") or "Labour",
                    "description": item.get("description", ""),
                    "qty": item.get("qty", "1"), "rate": item.get("rate", "0"),
                    "hsnCode": item.get("hsnCode", ""), "taxRate": item.get("taxRate", "0"),
                    "unit": item.get("unit") or "NOS",
                }
                if item.get("inventoryId"):
                    line["inventoryId"] = item["inventoryId"]
                if item.get("purchasePrice") is not None:
                    line["purchasePrice"] = item["purchasePrice"]
            if gstin and (not line["hsnCode"] or line.get("taxRate") in (None, "")):
                raise ValidationError({"items": f"Line {index}: enter the applicable HSN/SAC and GST percentage."})
            lines.append(line)

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
        items = service.items or payload.validated_data.get("items", [])
        if not items:
            raise ValidationError({"items": "Add at least one charge before reviewing the invoice."})
        _, _, _, amounts = self.quote(service, items)
        return Response(serialize_amounts(amounts))

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def close(self, request, pk=None):
        user = request.user
        company = Company.objects.select_for_update().get(pk=user.company_id)
        service = get_object_or_404(
            QuickService.objects.select_for_update().select_related("customer", "vehicle", "branch"),
            company=company, pk=self.get_object().pk,
        )
        if hasattr(service, "invoice"):
            return Response(self.get_serializer(service).data)

        payload = QuickServiceCloseSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        items = service.items or data.get("items", [])
        if not items:
            raise ValidationError({"items": "Add at least one charge before closing this Quick Service."})
        if any(row.get("inventoryId") for row in items) and not user_has_permission(user, "stock.edit"):
            raise PermissionDenied("Stock Edit permission is required to invoice and issue selected parts.")

        invoice_type, recipient, lines, amounts = self.quote(service, items)
        if data["expectedTotal"] != amounts["total"]:
            raise ValidationError({"expectedTotal": "The invoice total changed. Review the updated total before collecting payment."})
        account = WorkshopFinancialAccount.objects.filter(
            pk=data["account"], company=company, is_active=True,
        ).first()
        if not account:
            raise ValidationError({"account": "Select an active account owned by your workshop."})

        customer_snapshot = self._customer_snapshot(service, recipient.get("placeOfSupply", ""))
        vehicle_snapshot = self._vehicle_snapshot(service)
        doc = InvoiceSerializer(data={
            "date": timezone.localdate(), "invoiceType": invoice_type,
            "customer": customer_snapshot, "vehicle": vehicle_snapshot,
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
