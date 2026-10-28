from uuid import UUID
from django.db.models import Q
from django.db import transaction
from django.utils import timezone
from rest_framework import decorators,response,status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.renderers import JSONRenderer
from .renderers import InvoicePDFRenderer
from apps.accounts.permissions import user_has_permission
from common.viewsets import CompanyScopedModelViewSet
from .models import Invoice,EWayBill
from apps.jobs.audit import record_job_event
from .serializers import InvoiceSerializer,EWayBillSerializer
from .gst_rate_options import gst_rate_options

class InvoiceViewSet(CompanyScopedModelViewSet):
    queryset=Invoice.objects.select_related("customer","vehicle","job").prefetch_related("items").all()
    serializer_class=InvoiceSerializer
    action_permission_map = {
        "finalize": "invoices.edit",
        "pdf": "invoices.view",
        "cancel": "invoices.edit",
        "convert": "invoices.create",
        "gst_context": "invoices.view",
        "preview": "invoices.view",
    }

    @decorators.action(detail=False, methods=["get"], url_path="gst-context")
    def gst_context(self, request):
        from apps.jobs.india_gst import registration_state_diagnostics, gst_dropdown_presets
        company = request.user.company
        branch = request.user.branch
        gstin = str(company.gstin or "").strip().upper()
        registration = registration_state_diagnostics(company=company, branch=branch)
        return response.Response({
            "registered": bool(gstin),
            "gstin": gstin,
            "supplierState": registration["gstinStateCode"],
            "branchId": str(branch.pk) if branch else "",
            "branchName": str(branch.name) if branch else "",
            "branchState": str(branch.state or "") if branch else "",
            "supplyingState": registration["supplyingState"],
            "supplyingStateCode": registration["supplyingStateCode"],
            "stateSource": registration["stateSource"],
            "registrationStateMismatch": registration["registrationStateMismatch"],
            "companyName": company.legal_name or company.name,
            "companyState": company.state,
            "companyAddress": company.address,
            "companyCity": company.city,
            "companyPincode": company.pincode,
            "companyPhone": company.phone,
            "companyEmail": company.email,
            "invoiceType": "gst" if gstin else "regular",
            "gstRateOptions": gst_rate_options(),
            "gstDropdownPresets": gst_dropdown_presets(),
        })
    @decorators.action(detail=False, methods=["post"], url_path="preview")
    def preview(self, request):
        """Return authoritative totals without writing customer, invoice or stock."""
        from .calculations import calculate_invoice_amounts, serialize_amounts

        payload = request.data if isinstance(request.data, dict) else {}
        customer = payload.get("customer") or {}
        if not isinstance(customer, dict):
            raise ValidationError({"customer": "Customer details must be an object."})
        items = payload.get("items") or []
        if not isinstance(items, list) or len(items) > 200:
            raise ValidationError({"items": "Send up to 200 invoice line items."})
        if not all(isinstance(item, dict) for item in items):
            raise ValidationError({"items": "Invoice items must be objects."})
        company = request.user.company
        invoice_type = payload.get("invoiceType") or ("gst" if company.gstin else "regular")
        amounts = calculate_invoice_amounts(
            company=company,
            branch=request.user.branch,
            invoice_type=invoice_type,
            place_of_supply=customer.get("placeOfSupply") or
                            customer.get("stateCode") or customer.get("state") or "",
            items=items,
            discount=payload.get("discount", 0),
            adjustment=payload.get("adjustment", 0),
            paid=payload.get("paid", 0),
        )
        return response.Response(serialize_amounts(amounts))

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

    @decorators.action(detail=True, methods=["get"], url_path="pdf",
                       renderer_classes=[JSONRenderer, InvoicePDFRenderer])
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
        """Persist the same Decimal arithmetic returned by POST /preview."""
        from .calculations import persisted_invoice_amounts

        result = persisted_invoice_amounts(obj)
        obj.tax_mode = result["taxMode"]
        obj.taxable = result["taxable"]
        obj.cgst = result["cgst"]
        obj.sgst = result["sgst"]
        obj.igst = result["igst"]
        obj.total = result["total"]
        obj.balance = result["balance"]
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
            # Only invoices that consume stock require the additional Stock permission.
            if obj.items.filter(stock_item__isnull=False).exists() and not user_has_permission(request.user, "stock.edit"):
                raise PermissionDenied("Stock edit permission is required to finalize invoices containing stock items.")
            expected_type = "gst" if str(obj.company.gstin or "").strip() else "regular"
            if obj.invoice_type != expected_type:
                raise ValidationError({
                    "invoiceType": "Invoice type does not match workshop GST registration. Update Company Settings and recreate the draft as needed."
                })
            if obj.invoice_type == "gst":
                lines = list(obj.items.all())
                if not lines or any(not line.hsn_sac.strip() for line in lines):
                    raise ValidationError({"items": "Classify every GST invoice item with its applicable HSN/SAC before finalizing."})
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
        from .template_settings import template_for_document
        if not obj.template_snapshot:
            obj.template_snapshot = template_for_document(obj.company, obj.kind)
        obj.finalized_at=timezone.now()
        obj.save(update_fields=["status","finalized_at","template_snapshot","updated_at"])
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
    @transaction.atomic
    def convert(self,request,pk=None):
        src=self.get_object()
        if src.kind!="estimate": raise ValidationError("Only estimates can be converted.")
        invoice=Invoice.objects.create(company=src.company,branch=src.branch,
            seller_snapshot=src.seller_snapshot, customer_snapshot=src.customer_snapshot,
            vehicle_snapshot=src.vehicle_snapshot,
            tax_mode=src.tax_mode,
            number=f"INV-{timezone.now().year}-{Invoice.objects.filter(company=src.company,kind='invoice').count()+1:04d}",kind="invoice",invoice_type=src.invoice_type,status="Draft",date=timezone.localdate(),customer=src.customer,vehicle=src.vehicle,job=src.job,notes=src.notes,discount=src.discount,adjustment=src.adjustment,payment_mode=src.payment_mode,payment_terms=src.payment_terms,source_estimate=src)
        for line in src.items.all():
            line.pk=None; line.invoice=invoice; line.save()
        # Converted estimates start as new draft invoices and use the active
        # invoice design when finalized, not the source estimate's appearance.
        invoice.template_snapshot = {}
        self._recalculate(invoice); src.status="Converted"; src.save(update_fields=["status","updated_at"])
        if invoice.job_id:
            record_job_event(invoice.job, request.user, "Estimate Converted",
                             f"{src.number} converted to {invoice.number}", {"invoiceId": str(invoice.pk)})
        return response.Response(InvoiceSerializer(invoice).data,status=status.HTTP_201_CREATED)

class EWayBillViewSet(CompanyScopedModelViewSet):
    queryset = EWayBill.objects.select_related("invoice").all()
    serializer_class = EWayBillSerializer

    @decorators.action(detail=False, methods=["get"], url_path="from-context")
    def from_context(self, request):
        """Authenticated workshop dispatch party used for new OUTWARD drafts."""
        from .eway_validation import workshop_supply_party
        return response.Response({
            "from": workshop_supply_party(request.user.company, request.user.branch),
            "gstRateOptions": gst_rate_options(),
            "branchId": str(request.user.branch.pk) if request.user.branch else "",
        })
    action_permission_map = {
        "generate": "invoices.edit",
        "reset_draft": "invoices.edit",
        "pdf": "invoices.view",
    }

    @decorators.action(detail=True, methods=["get"], url_path="pdf",
                       renderer_classes=[JSONRenderer, InvoicePDFRenderer])
    def pdf(self, request, pk=None):
        """Tenant-scoped saved E-Way Bill PDF, with correct PDF Accept handling."""
        from .eway_pdf import eway_pdf_response
        return eway_pdf_response(self.get_object())

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.status != "Draft" or obj.number:
            raise ValidationError({
                "status": "Only local drafts can be deleted. An official E-Way Bill cannot be deleted here."
            })
        return super().destroy(request, *args, **kwargs)

    @decorators.action(detail=True, methods=["post"], url_path="reset-draft")
    @transaction.atomic
    def reset_draft(self, request, pk=None):
        """Reopen a LOCAL prepared draft; never modify an NIC-issued bill."""
        obj = self.get_object()
        if obj.number or obj.status not in {"Draft", "Ready for API"}:
            raise ValidationError({
                "status": "Only locally prepared drafts can be reset. Cancel official bills through the GST portal."
            })
        obj.status = "Draft"
        obj.save(update_fields=["status", "updated_at"])
        return response.Response(self.get_serializer(obj).data)

    @decorators.action(detail=True, methods=["post"])
    @transaction.atomic
    def generate(self, request, pk=None):
        """Prepare for future NIC API handoff. Never invent an official EBN."""
        from .eway_validation import validate_preparation
        obj = self.get_object()
        if obj.number or obj.status not in {"Draft", "Ready for API"}:
            raise ValidationError({"status": "This E-Way Bill cannot be prepared again."})
        payload = dict(obj.payload or {})
        if payload.get("supplyType", "Outward") == "Outward":
            from .eway_validation import workshop_supply_party
            payload["from"] = workshop_supply_party(obj.company, obj.branch)
        validate_preparation(payload)
        if payload != (obj.payload or {}):
            obj.payload = payload
            obj.save(update_fields=["payload", "updated_at"])
        if obj.status == "Draft":
            obj.status = "Ready for API"
            obj.save(update_fields=["status", "updated_at"])
        return response.Response(self.get_serializer(obj).data)
