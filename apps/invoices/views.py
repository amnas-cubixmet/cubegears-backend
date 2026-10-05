from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework import decorators,response,status
from rest_framework.exceptions import ValidationError
from common.viewsets import CompanyScopedModelViewSet
from .models import Invoice,EWayBill
from .serializers import InvoiceSerializer,EWayBillSerializer

class InvoiceViewSet(CompanyScopedModelViewSet):
    queryset=Invoice.objects.select_related("customer","vehicle","job").prefetch_related("items").all()
    serializer_class=InvoiceSerializer
    def get_queryset(self):
        qs=super().get_queryset(); kind=self.request.query_params.get("kind")
        return qs.filter(kind=kind) if kind else qs

    def perform_create(self,serializer):
        company=self.request.user.company
        kind=serializer.validated_data.get("kind","invoice")
        count=Invoice.objects.filter(company=company,kind=kind).count()+1
        prefix="EST" if kind=="estimate" else "INV"
        number=serializer.validated_data.get("number") or f"{prefix}-{timezone.now().year}-{count:04d}"
        obj=serializer.save(company=company,branch=self.request.user.branch,number=number)
        self._recalculate(obj)

    def perform_update(self,serializer):
        obj=serializer.save(); self._recalculate(obj)

    def _recalculate(self,obj):
        subtotal=Decimal("0"); tax=Decimal("0")
        for item in obj.items.all():
            taxable=max(Decimal("0"),item.quantity*item.rate-item.discount)
            subtotal += taxable; tax += taxable*item.tax_rate/Decimal("100")
        obj.taxable=max(Decimal("0"),subtotal-obj.discount)
        if obj.invoice_type=="gst":
            obj.igst=tax
        else:
            obj.igst=Decimal("0")
        obj.total=obj.taxable+obj.cgst+obj.sgst+obj.igst+obj.adjustment
        obj.balance=max(Decimal("0"),obj.total-obj.paid)
        obj.save(update_fields=["taxable","igst","total","balance","updated_at"])

    @decorators.action(detail=True,methods=["post"])
    @transaction.atomic
    def finalize(self,request,pk=None):
        from apps.inventory.models import StockItem,StockMovement
        obj=self.get_object()
        if obj.status in {"Finalized","Paid","Issued","Converted"}: return response.Response(InvoiceSerializer(obj).data)
        if obj.kind=="invoice":
            for line in obj.items.select_related("stock_item"):
                if not line.stock_item_id: continue
                item=StockItem.objects.select_for_update().get(pk=line.stock_item_id,company=obj.company)
                if item.available_quantity < line.quantity: raise ValidationError(f"Insufficient stock for {item.name}.")
                item.on_hand -= line.quantity; item.save(update_fields=["on_hand","updated_at"])
                StockMovement.objects.create(company=obj.company,branch=obj.branch,item=item,movement_type="sale",quantity=-line.quantity,reference=obj.number,created_by=request.user)
            obj.status="Paid" if obj.balance<=0 else "Finalized"
        else: obj.status="Issued"
        obj.finalized_at=timezone.now(); obj.save(update_fields=["status","finalized_at","updated_at"])
        return response.Response(InvoiceSerializer(obj).data)

    @decorators.action(detail=True,methods=["post"])
    def cancel(self,request,pk=None):
        obj=self.get_object(); obj.status="Cancelled"; obj.cancelled_at=timezone.now(); obj.save(update_fields=["status","cancelled_at","updated_at"])
        return response.Response(InvoiceSerializer(obj).data)

    @decorators.action(detail=True,methods=["post"],url_path="convert-to-invoice")
    def convert(self,request,pk=None):
        src=self.get_object()
        if src.kind!="estimate": raise ValidationError("Only estimates can be converted.")
        invoice=Invoice.objects.create(company=src.company,branch=src.branch,number=f"INV-{timezone.now().year}-{Invoice.objects.filter(company=src.company,kind='invoice').count()+1:04d}",kind="invoice",invoice_type=src.invoice_type,status="Draft",date=timezone.localdate(),customer=src.customer,vehicle=src.vehicle,job=src.job,notes=src.notes,discount=src.discount,adjustment=src.adjustment,payment_mode=src.payment_mode,payment_terms=src.payment_terms,source_estimate=src)
        for line in src.items.all():
            line.pk=None; line.invoice=invoice; line.save()
        self._recalculate(invoice); src.status="Converted"; src.save(update_fields=["status","updated_at"])
        return response.Response(InvoiceSerializer(invoice).data,status=status.HTTP_201_CREATED)

class EWayBillViewSet(CompanyScopedModelViewSet):
    queryset=EWayBill.objects.select_related("invoice").all()
    serializer_class=EWayBillSerializer

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
