from copy import deepcopy
from django.db import transaction
from django.utils import timezone
from apps.accounts.permissions import RolePermission
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError

from .models import Job
from .audit import record_job_event
from apps.inventory.models import StockItem, StockMovement

def _job(request, job_id):
    qs=Job.objects.filter(pk=job_id)
    if not request.user.is_superuser:
        qs=qs.filter(company=request.user.company)
    from django.shortcuts import get_object_or_404
    return get_object_or_404(qs)

def _state(job):
    data=deepcopy(job.parts_workflow or {})
    data.setdefault("lines",[])
    data.setdefault("transactions",[])
    data.setdefault("purchaseOrders",[])
    return data

def _save(job,state):
    job.parts_workflow=state
    job.save(update_fields=["parts_workflow","updated_at"])

def _line_state(line, item=None):
    issues=line.get("issues") or []
    returns=line.get("returns") or []
    gross=sum(float(x.get("qty") or 0) for x in issues)
    returned=sum(float(x.get("qty") or 0) for x in returns)
    net=max(0,gross-returned)
    requested=float(line.get("requestedQty") or 0)
    pending=max(0,requested-net)
    on_hand=float(item.on_hand) if item else 0
    available=max(0,on_hand-float(item.reserved)) if item else 0
    stock_state="Available"
    if pending>0 and available<=0: stock_state="Order Required"
    elif pending>0 and available<pending: stock_state="Partial"
    return {**line,"qtyOnHand":on_hand,"availableQty":available,"grossIssued":gross,"returnedQty":returned,"netIssued":net,"pendingQty":pending,"stockState":stock_state}

class PartsAvailabilityView(APIView):
    permission_classes=[RolePermission]
    permission_map={"GET": "jobs.view", "POST": ["jobs.edit", "stock.edit"]}
    def get(self,request,job_id):
        job=_job(request,job_id); state=_state(job); lines=[]
        for line in state["lines"]:
            item=StockItem.objects.filter(pk=line.get("partId"),company=job.company).first()
            lines.append(_line_state(line,item))
        return Response({"lines":lines,"purchaseOrders":state["purchaseOrders"]})

class JobIssuesView(APIView):
    permission_classes=[RolePermission]
    permission_map={"GET": "jobs.view", "POST": ["jobs.edit", "stock.edit"]}
    def get(self,request,job_id):
        job=_job(request,job_id); state=_state(job); lines=[]
        for line in state["lines"]:
            item=StockItem.objects.filter(pk=line.get("partId"),company=job.company).first()
            lines.append(_line_state(line,item))
        return Response({"lines":lines})
    @transaction.atomic
    def post(self,request,job_id):
        job=_job(request,job_id); state=_state(job)
        for req_line in request.data.get("lines",[]):
            line=next((x for x in state["lines"] if str(x.get("id"))==str(req_line.get("job_card_part_id"))),None)
            if not line: raise ValidationError("Part line not found.")
            item=StockItem.objects.select_for_update().get(pk=line.get("partId"),company=job.company)
            qty=float(req_line.get("issue_qty") or 0)
            current=_line_state(line,item)
            if qty<=0 or qty>current["pendingQty"] or qty>current["availableQty"]:
                raise ValidationError("Invalid issue quantity or insufficient stock.")
            item.on_hand=float(item.on_hand)-qty; item.save(update_fields=["on_hand","updated_at"])
            issue={"id":f"ISS-{timezone.now().timestamp()}","issueNo":f"ISS-{timezone.now().timestamp()}","qty":qty,"issuedTo":req_line.get("issued_to") or "Workshop","issuedBy":request.user.name,"issuedAt":timezone.now().isoformat()}
            line.setdefault("issues",[]).insert(0,issue)
            state["transactions"].insert(0,{"id":issue["id"],"type":"Issue","partName":line.get("partName"),"qty":qty,"reference":issue["issueNo"],"createdAt":issue["issuedAt"],"createdBy":request.user.name})
            StockMovement.objects.create(company=job.company,branch=job.branch,item=item,movement_type="job_issue",quantity=-qty,unit_cost=item.cost_price,reference=job.job_number,job=job,created_by=request.user)
            record_job_event(job, request.user, "Part Issued", f"{item.name} × {qty}",
                             {"itemId": str(item.pk), "issueId": issue["id"]})
        _save(job,state); return Response(state)

class JobReturnsView(APIView):
    permission_classes=[RolePermission]
    permission_map={"GET": "jobs.view", "POST": ["jobs.edit", "stock.edit"]}
    @transaction.atomic
    def post(self,request,job_id):
        job=_job(request,job_id); state=_state(job)
        for req_line in request.data.get("lines",[]):
            line=next((x for x in state["lines"] if str(x.get("id"))==str(req_line.get("job_card_part_id"))),None)
            if not line: raise ValidationError("Part line not found.")
            item=StockItem.objects.select_for_update().get(pk=line.get("partId"),company=job.company)
            qty=float(req_line.get("return_qty") or 0); current=_line_state(line,item)
            if qty<=0 or qty>current["netIssued"]: raise ValidationError("Invalid return quantity.")
            disposition=req_line.get("disposition") or "Reusable"
            if disposition=="Reusable":
                item.on_hand=float(item.on_hand)+qty; item.save(update_fields=["on_hand","updated_at"])
                StockMovement.objects.create(company=job.company,branch=job.branch,item=item,movement_type="return",quantity=qty,unit_cost=item.cost_price,reference=job.job_number,job=job,created_by=request.user)
            ret={"id":f"RET-{timezone.now().timestamp()}","returnNo":f"RET-{timezone.now().timestamp()}","qty":qty,"disposition":disposition,"returnedBy":request.user.name,"returnedAt":timezone.now().isoformat()}
            line.setdefault("returns",[]).insert(0,ret)
            state["transactions"].insert(0,{"id":ret["id"],"type":"Return","partName":line.get("partName"),"qty":qty,"reference":ret["returnNo"],"createdAt":ret["returnedAt"],"createdBy":request.user.name})
            record_job_event(job, request.user, "Part Returned", f"{item.name} × {qty}",
                             {"itemId": str(item.pk), "returnId": ret["id"]})
        _save(job,state); return Response(state)

class CompletionEligibilityView(APIView):
    permission_classes=[RolePermission]
    permission_map={"GET": "jobs.view", "POST": ["jobs.edit", "stock.edit"]}
    def get(self,request,job_id):
        job=_job(request,job_id); state=_state(job); blocking=[]
        for line in state["lines"]:
            if line.get("lineStatus")=="Cancelled": continue
            item=StockItem.objects.filter(pk=line.get("partId"),company=job.company).first()
            row=_line_state(line,item)
            if row["pendingQty"]>0: blocking.append({"lineId":line.get("id"),"partName":line.get("partName"),"pendingQty":row["pendingQty"],"reason":f"{line.get('partName')}: {row['pendingQty']} pending"})
        return Response({"can_complete":not blocking,"blocking_reasons":blocking})

class PartsTransactionsView(APIView):
    permission_classes=[RolePermission]
    permission_map={"GET": "jobs.view", "POST": ["jobs.edit", "stock.edit"]}
    def get(self,request,job_id):
        job=_job(request,job_id); return Response({"items":_state(job)["transactions"]})

class JobPurchaseOrderView(APIView):
    permission_classes=[RolePermission]
    permission_map={"GET": "jobs.view", "POST": ["jobs.edit", "stock.edit"]}
    def post(self,request,job_id):
        job=_job(request,job_id); state=_state(job)
        line=next((x for x in state["lines"] if str(x.get("id"))==str(request.data.get("job_card_part_id"))),None)
        if not line: raise ValidationError("Part line not found.")
        po={"id":f"PO-{timezone.now().timestamp()}","poNo":f"PO-{timezone.now().timestamp()}","jobId":str(job.id),"lineId":line.get("id"),"partId":line.get("partId"),"partName":line.get("partName"),"vendor":request.data.get("vendor") or "Supplier","type":request.data.get("type") or "Cash","orderDate":timezone.localdate().isoformat(),"requestedQty":line.get("requestedQty") or 0,"orderedQty":float(request.data.get("ordered_qty") or 0),"unitCost":float(request.data.get("unit_cost") or 0),"discount":float(request.data.get("discount") or 0),"tax":float(request.data.get("tax") or 0),"receivedQty":0,"status":"Ordered"}
        state["purchaseOrders"].insert(0,po); _save(job,state)
        record_job_event(job, request.user, "Purchase Order Created",
                         f"{po['partName']} ordered from {po['vendor']}",
                         {"orderId": po["id"]})
        return Response(po,status=201)

class PurchaseOrderInwardView(APIView):
    permission_classes=[RolePermission]
    permission_map={"GET": "jobs.view", "POST": ["jobs.edit", "stock.edit"]}
    @transaction.atomic
    def post(self,request,po_id):
        qs=Job.objects.filter(company=request.user.company) if not request.user.is_superuser else Job.objects.all()
        job=None; po=None
        for candidate in qs:
            state=_state(candidate)
            po=next((x for x in state["purchaseOrders"] if str(x.get("id"))==str(po_id)),None)
            if po: job=candidate; break
        if not job or not po: raise ValidationError("Purchase order not found.")
        qty=float(request.data.get("inwardQty") or 0)
        if qty<=0: raise ValidationError("Inward quantity must be greater than zero.")
        item=StockItem.objects.select_for_update().get(pk=po.get("partId"),company=job.company)
        item.on_hand=float(item.on_hand)+qty; item.save(update_fields=["on_hand","updated_at"])
        po["receivedQty"]=float(po.get("receivedQty") or 0)+qty
        po["status"]="Received" if po["receivedQty"]>=float(po.get("orderedQty") or 0) else "Partially Received"
        po.update({"billNo":request.data.get("billNo") or "","billDate":request.data.get("billDate") or "","taxType":request.data.get("taxType") or "GST","rack":request.data.get("rack") or "","barcode":request.data.get("barcode") or ""})
        state=_state(job)
        for i,x in enumerate(state["purchaseOrders"]):
            if str(x.get("id"))==str(po_id): state["purchaseOrders"][i]=po
        state["transactions"].insert(0,{"id":f"GRN-{timezone.now().timestamp()}","type":"Inward","partName":po.get("partName"),"qty":qty,"reference":po.get("poNo"),"createdAt":timezone.now().isoformat(),"createdBy":request.user.name})
        _save(job,state)
        StockMovement.objects.create(company=job.company,branch=job.branch,item=item,movement_type="purchase",quantity=qty,reference=po.get("poNo") or "",job=job,created_by=request.user)
        record_job_event(job, request.user, "Parts Received",
                         f"{item.name} × {qty} received", {"orderId": po_id})
        return Response(po)



# Outside shop purchasing is a distinct approval workflow, not a stock movement.
# Historic rows without "status" are already purchased and stay in the ledger.
def _outside_job_locked(request, job_id):
    from django.shortcuts import get_object_or_404
    qs = Job.objects.select_for_update()
    if not request.user.is_superuser:
        qs = qs.filter(company=request.user.company)
    job = get_object_or_404(qs, pk=job_id)
    if job.status == Job.STATUS_DELIVERED:
        raise ValidationError({"job": "Outside purchases cannot change after delivery."})
    return job


def _outside_record(job, purchase_id):
    from django.http import Http404
    return next(
        (row for row in _state(job).get("outsideParts", [])
         if str(row.get("id")) == str(purchase_id)),
        None,
    )


def _outside_status(record):
    return record.get("status") or "Purchased"  # Preserve historic records.


def _outside_activity(job, user, event, record):
    from .models import JobActivity
    JobActivity.objects.create(
        company=job.company, branch=job.branch, job=job, actor=user,
        event=event, description=f"{record['partName']} · {record['vendor']}",
        metadata={"outsidePartId": record["id"], "supplier": record["vendor"]},
    )


class OutsidePartsView(APIView):
    """Create an outside-part quotation REQUEST; do not buy/charge before approval."""
    from apps.accounts.permissions import RolePermission
    permission_classes = [RolePermission]
    permission_map = {"GET": "jobs.view", "POST": "jobs.edit"}

    def get(self, request, job_id):
        job = _job(request, job_id)
        return Response({"items": _state(job).get("outsideParts", [])})

    @transaction.atomic
    def post(self, request, job_id):
        from decimal import Decimal, InvalidOperation
        from uuid import uuid4
        from rest_framework import status as drf_status
        job = _outside_job_locked(request, job_id)
        key = str(request.data.get("requestId") or "").strip()
        state = _state(job)
        records = state.setdefault("outsideParts", [])
        previous = next((part for part in records if part.get("requestId") == key), None) if key else None
        if previous:
            return Response(previous)
        name = str(request.data.get("partName") or "").strip()
        vendor = str(request.data.get("vendor") or "").strip()
        if not name or len(name) > 160 or not vendor or len(vendor) > 160 or not key or len(key) > 100:
            raise ValidationError({"part": "Part name, supplier and request ID are required."})
        try:
            qty = Decimal(str(request.data.get("quantity", 0)))
            cost = Decimal(str(request.data.get("unitCost", 0)))
            selling = Decimal(str(request.data.get("sellingPrice", 0)))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError({"amount": "Invalid quantity or amount."})
        if (not all(v.is_finite() for v in (qty, cost, selling))
                or qty <= 0 or cost < 0 or selling < 0):
            raise ValidationError({"amount": "Quantity must be positive; amounts cannot be negative."})
        if qty > Decimal("999999.99") or cost > Decimal("999999999.99") or selling > Decimal("999999999.99"):
            raise ValidationError({"amount": "Quantity or price is too large."})
        quote = (qty * cost).quantize(Decimal("0.01"))
        record = {
            "id": str(uuid4()), "requestId": key, "status": "Pending Approval",
            "partName": name, "sku": str(request.data.get("sku") or "")[:80],
            "vendor": vendor, "invoiceNo": "",
            "quantity": str(qty), "unitCost": str(cost), "sellingPrice": str(selling),
            "quotedTotal": str(quote), "totalCost": "0.00",
            "totalSelling": "0.00",
            "createdAt": timezone.now().isoformat(), "createdBy": request.user.name,
        }
        records.insert(0, record)
        _save(job, state)
        _outside_activity(job, request.user, "Outside Part Approval Requested", record)
        return Response(record, status=drf_status.HTTP_201_CREATED)


class OutsidePartDecisionView(APIView):
    """Financial approval is separate from creating the supplier quotation."""
    from apps.accounts.permissions import RolePermission
    permission_classes = [RolePermission]
    permission_map = {"POST": ["jobs.view", "expenses.edit"]}

    @transaction.atomic
    def post(self, request, job_id, purchase_id):
        from django.shortcuts import get_object_or_404
        job = _outside_job_locked(request, job_id)
        state = _state(job)
        record = next((item for item in state.get("outsideParts", [])
                       if str(item.get("id")) == str(purchase_id)), None)
        if not record:
            return Response({"detail": "Purchase request not found."}, status=404)
        choice = str(request.data.get("decision") or "").strip().lower()
        if choice not in {"approve", "reject"}:
            raise ValidationError({"decision": "Choose approve or reject."})
        if _outside_status(record) != "Pending Approval":
            raise ValidationError({"status": "Only pending requests can be decided."})
        record["status"] = "Approved" if choice == "approve" else "Rejected"
        record["decisionAt"] = timezone.now().isoformat()
        record["decidedBy"] = request.user.name
        _save(job, state)
        _outside_activity(job, request.user,
                          "Outside Part Approved" if choice == "approve" else "Outside Part Rejected", record)
        return Response(record)


class OutsidePartCompleteView(APIView):
    """Only approved purchases can be recorded, with a validated private supplier bill."""
    from apps.accounts.permissions import RolePermission
    from rest_framework.parsers import MultiPartParser, FormParser
    permission_classes = [RolePermission]
    permission_map = {"POST": ["jobs.edit", "expenses.view"]}
    parser_classes = [MultiPartParser, FormParser]

    @transaction.atomic
    def post(self, request, job_id, purchase_id):
        from pathlib import Path
        from decimal import Decimal, InvalidOperation
        from .models import OutsidePartBill
        job = _outside_job_locked(request, job_id)
        state = _state(job)
        record = next((item for item in state.get("outsideParts", [])
                       if str(item.get("id")) == str(purchase_id)), None)
        if not record:
            return Response({"detail": "Purchase request not found."}, status=404)
        if _outside_status(record) == "Purchased":
            return Response(record)
        if _outside_status(record) != "Approved":
            raise ValidationError({"status": "Manager approval is required before purchasing."})
        bill = request.FILES.get("bill")
        if bill is None or bill.size < 1 or bill.size > 8 * 1024 * 1024:
            raise ValidationError({"bill": "Attach a supplier bill (PDF/image, maximum 8 MB)."})
        mime = str(bill.content_type or "").split(";")[0].lower()
        valid = {
            "application/pdf": (".pdf", lambda v: v.startswith(b"%PDF-")),
            "image/jpeg": (".jpg", lambda v: v.startswith(b"\xff\xd8\xff")),
            "image/png": (".png", lambda v: v.startswith(b"\x89PNG\r\n\x1a\n")),
            "image/webp": (".webp", lambda v: v.startswith(b"RIFF") and v[8:12] == b"WEBP"),
        }
        bill.seek(0)
        header = bill.read(16)
        bill.seek(0)
        if mime not in valid or not valid[mime][1](header):
            raise ValidationError({"bill": "Only genuine PDF, JPEG, PNG or WebP files are accepted."})
        invoice = str(request.data.get("invoiceNo") or "").strip()
        if not invoice or len(invoice) > 120:
            raise ValidationError({"invoiceNo": "Enter a supplier bill number (max 120 characters)."})
        try:
            actual = Decimal(str(request.data.get("unitCost") or record["unitCost"]))
            qty = Decimal(str(record["quantity"]))
        except (InvalidOperation, ValueError, TypeError):
            raise ValidationError({"unitCost": "Enter a valid actual purchase rate."})
        if not actual.is_finite() or actual < 0 or actual > Decimal("999999999.99"):
            raise ValidationError({"unitCost": "Invalid purchase rate."})
        quoted_total = Decimal(str(record.get("quotedTotal") or qty * Decimal(str(record["unitCost"]))))
        actual_total = (actual * qty).quantize(Decimal("0.01"))
        if actual_total > quoted_total:
            raise ValidationError({"unitCost": "Actual cost exceeds the approved quote. Request approval again."})

        attached = OutsidePartBill(
            company=job.company, branch=job.branch, job=job,
            purchase_id=purchase_id, uploaded_by=request.user,
            original_filename=Path(bill.name).name[:180],
        )
        attached.file.save(f"supplier-bill{valid[mime][0]}", bill, save=False)
        try:
            attached.save()
            record.update({
                "status": "Purchased", "invoiceNo": invoice,
                "unitCost": str(actual), "totalCost": str(actual_total),
                "totalSelling": str((qty * Decimal(str(record["sellingPrice"]))).quantize(Decimal("0.01"))),
                "billId": str(attached.id),
                "usedAt": timezone.now().isoformat(), "purchasedBy": request.user.name,
            })
            _save(job, state)
            _outside_activity(job, request.user, "Outside Part Purchased", record)
        except Exception:
            attached.file.delete(save=False)
            raise
        return Response(record)


class OutsidePartBillView(APIView):
    """Serve the original supplier file only to company-authorized users."""
    from apps.accounts.permissions import RolePermission
    permission_classes = [RolePermission]
    permission_map = {"GET": ["jobs.view", "expenses.view"]}

    def get(self, request, job_id, purchase_id):
        from django.http import FileResponse
        from django.shortcuts import get_object_or_404
        from .models import OutsidePartBill
        job = _job(request, job_id)
        record = _outside_record(job, purchase_id)
        if not record or _outside_status(record) != "Purchased":
            return Response({"detail": "Purchase bill not found."}, status=404)
        bill = get_object_or_404(
            OutsidePartBill, company=job.company, job=job, purchase_id=purchase_id
        )
        bill.file.open("rb")
        suffix = bill.file.name.rsplit(".", 1)[-1].lower()
        mime = {"pdf": "application/pdf", "jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(suffix, "application/octet-stream")
        result = FileResponse(
            bill.file, as_attachment=True, filename=bill.original_filename, content_type=mime
        )
        result["Cache-Control"] = "private, no-store"
        result["X-Content-Type-Options"] = "nosniff"
        return result
