from decimal import Decimal
from uuid import UUID
from django.db.models import Q
from django.db import transaction
from django.utils import timezone
from rest_framework import decorators,response,status
from rest_framework.exceptions import ValidationError
from common.viewsets import CompanyScopedModelViewSet
from .models import Invoice,EWayBill
from apps.jobs.audit import record_job_event
from .serializers import InvoiceSerializer,EWayBillSerializer

class InvoiceViewSet(CompanyScopedModelViewSet):
    queryset=Invoice.objects.select_related("customer","vehicle","job").prefetch_related("items").all()
    serializer_class=InvoiceSerializer
    action_permission_map = {
        "finalize": ["invoices.edit", "stock.edit"],
        "pdf": "invoices.view",
        "cancel": "invoices.edit",
        "convert": "invoices.create",
        "gst_context": "invoices.view",
    }

    @decorators.action(detail=False, methods=["get"], url_path="gst-context")
    def gst_context(self, request):
        from apps.jobs.india_gst import state_code
        company = request.user.company
        gstin = str(company.gstin or "").strip().upper()
        return response.Response({
            "registered": bool(gstin),
            "gstin": gstin,
            "supplierState": state_code(gstin[:2] if len(gstin) == 15 else ""),
            "companyName": company.legal_name or company.name,
            "companyState": company.state,
        })
    def get_queryset(self):
        qs = super().get_queryset()
        kind = self.request.query_params.get("kind")
        if kind:
            qs = qs.filter(kind=kind)
        job_id = self.request.query_params.get("jobId")
        if job_id:
            try:
                UUID(str(job_id))
            except (ValueError, TypeError, AttributeError):
                return qs.none()
            qs = qs.filter(Q(job_id=job_id) | Q(source_job_id=str(job_id)))
        return qs

    @decorators.action(detail=True, methods=["get"], url_path="pdf")
    def pdf(self, request, pk=None):
        # get_object enforces the same company scoping as invoice detail.
        # invoices.view is required, even for a finalized invoice.
        from .pdf import pdf_response
        invoice = self.get_object()
        return pdf_response(invoice)

    @transaction.atomic
    def perform_create(self,serializer):
        company=self.request.user.company
        kind=serializer.validated_data.get("kind","invoice")
        count=Invoice.objects.filter(company=company,kind=kind).count()+1
        prefix="EST" if kind=="estimate" else "INV"
        number=serializer.validated_data.get("number") or f"{prefix}-{timezone.now().year}-{count:04d}"
        obj=serializer.save(company=company,branch=self.request.user.branch,number=number)
        self._recalculate(obj)
        if obj.job_id:
            record_job_event(obj.job, self.request.user, 'Invoice Created',
                             f'{obj.number} created', {'invoiceId': str(obj.pk)})

    @transaction.atomic
    def perform_update(self,serializer):
        obj=serializer.save(); self._recalculate(obj)
        if obj.job_id:
            record_job_event(obj.job, self.request.user, 'Invoice Updated',
                             f'{obj.number} updated', {'invoiceId': str(obj.pk)})

    def _recalculate(self,obj):
        """Recompute totals from saved lines; client-provided CGST/SGST/IGST are never trusted."""
        from .models import Invoice
        from apps.jobs.india_gst import gst_context, gst_rate, calculate_tax
        from decimal import ROUND_HALF_UP

        # GST supply state derives from the workshop's registration and legal POS,
        # not a client tax-mode dropdown or vehicle registration number.
        if obj.invoice_type == "gst":
            customer = obj.customer
            pos = (obj.customer_snapshot or {}).get("placeOfSupply") or (
                obj.customer_snapshot or {}
            ).get("stateCode") or customer.state
            ctx = gst_context(
                company=obj.company, branch=obj.branch, customer=customer,
                config={"mode": "gst", "placeOfSupply": pos},
            )
            tax_mode = ctx["taxMode"]
        else:
            tax_mode = "none"

        rows = []
        for item in obj.items.all():
            if item.quantity <= 0 or item.rate < 0:
                raise ValidationError({"items": "Item quantity must be positive and rate cannot be negative."})
            gross = (item.quantity * item.rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if item.discount < 0 or item.discount > gross:
                raise ValidationError({"items": "Item discount cannot exceed its amount."})
            rate = gst_rate(item.tax_rate) if tax_mode != "none" else Decimal("0")
            rows.append({
                "total": str(gross - item.discount), "taxRate": str(rate),
            })
        result = calculate_tax(rows, obj.discount, tax_mode)
        obj.tax_mode = tax_mode
        obj.taxable = result["taxable"]
        obj.cgst = result["cgst"]
        obj.sgst = result["sgst"]
        obj.igst = result["igst"]
        obj.total = result["total"] + obj.adjustment
        if obj.total < 0:
            raise ValidationError({"adjustment": "Adjustment cannot make the invoice total negative."})
        obj.balance = max(Decimal("0"), obj.total - obj.paid)
        obj.save(update_fields=[
            "tax_mode", "taxable", "cgst", "sgst", "igst",
            "total", "balance", "updated_at",
        ])

    @decorators.action(detail=True,methods=["post"])
    @transaction.atomic
    def finalize(self,request,pk=None):
        from apps.inventory.models import StockItem,StockMovement
        obj=self.get_object()
        if obj.status in {"Finalized","Paid","Issued","Converted"}: return response.Response(InvoiceSerializer(obj).data)
        if obj.kind=="invoice":
            for line in obj.items.select_related("stock_item"):
                if not line.stock_item_id: continue
                try:
                    item=StockItem.objects.select_for_update().get(pk=line.stock_item_id,company=obj.company)
                except StockItem.DoesNotExist:
                    raise ValidationError("An invoice item references stock from another company or a missing stock item.")
                if item.available_quantity < line.quantity: raise ValidationError(f"Insufficient stock for {item.name}.")
                item.on_hand -= line.quantity; item.save(update_fields=["on_hand","updated_at"])
                StockMovement.objects.create(company=obj.company,branch=obj.branch,item=item,movement_type="sale",quantity=-line.quantity,reference=obj.number,created_by=request.user)
            obj.status="Paid" if obj.balance<=0 else "Finalized"
        else: obj.status="Issued"
        obj.finalized_at=timezone.now(); obj.save(update_fields=["status","finalized_at","updated_at"])
        if obj.job_id:
            record_job_event(obj.job, request.user, "Invoice Finalized",
                             f"{obj.number} {obj.status.lower()}", {"invoiceId": str(obj.pk)})
        return response.Response(InvoiceSerializer(obj).data)

    @decorators.action(detail=True,methods=["post"])
    def cancel(self,request,pk=None):
        obj=self.get_object(); obj.status="Cancelled"; obj.cancelled_at=timezone.now(); obj.save(update_fields=["status","cancelled_at","updated_at"])
        if obj.job_id:
            record_job_event(obj.job, request.user, "Invoice Cancelled",
                             f"{obj.number} cancelled", {"invoiceId": str(obj.pk)})
        return response.Response(InvoiceSerializer(obj).data)

    @decorators.action(detail=True,methods=["post"],url_path="convert-to-invoice")
    def convert(self,request,pk=None):
        src=self.get_object()
        if src.kind!="estimate": raise ValidationError("Only estimates can be converted.")
        invoice=Invoice.objects.create(company=src.company,branch=src.branch,number=f"INV-{timezone.now().year}-{Invoice.objects.filter(company=src.company,kind='invoice').count()+1:04d}",kind="invoice",invoice_type=src.invoice_type,status="Draft",date=timezone.localdate(),customer=src.customer,vehicle=src.vehicle,job=src.job,notes=src.notes,discount=src.discount,adjustment=src.adjustment,payment_mode=src.payment_mode,payment_terms=src.payment_terms,source_estimate=src)
        for line in src.items.all():
            line.pk=None; line.invoice=invoice; line.save()
        self._recalculate(invoice); src.status="Converted"; src.save(update_fields=["status","updated_at"])
        if invoice.job_id:
            record_job_event(invoice.job, request.user, "Estimate Converted",
                             f"{src.number} converted to {invoice.number}", {"invoiceId": str(invoice.pk)})
        return response.Response(InvoiceSerializer(invoice).data,status=status.HTTP_201_CREATED)

class EWayBillViewSet(CompanyScopedModelViewSet):
    queryset=EWayBill.objects.select_related("invoice").all()
    serializer_class=EWayBillSerializer
    action_permission_map={"generate": "invoices.edit"}

    @decorators.action(detail=True,methods=["post"])
    def generate(self,request,pk=None):
        obj=self.get_object()
        payload=dict(obj.payload or {})
        required=[
            payload.get("supplyType"),
            payload.get("subSupplyType"),
            payload.get("transactionType"),
            payload.get("documentType"),
            payload.get("documentNo"),
            payload.get("documentDate"),
        ]
        if not all(required):
            raise ValidationError("Complete the required E-Way Bill fields before submitting.")
        items=payload.get("items") or []
        if not items:
            raise ValidationError("Add at least one goods item.")
        obj.status="Ready for API"
        payload["status"]="Ready for API"
        payload["updatedAt"]=timezone.now().isoformat()
        obj.payload=payload
        obj.save(update_fields=["status","payload","updated_at"])
        return response.Response(EWayBillSerializer(obj).data)
