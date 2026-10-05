from copy import deepcopy
from django.db import transaction
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError

from .models import Job
from apps.inventory.models import StockItem, StockMovement

def _job(request, job_id):
    qs=Job.objects.filter(pk=job_id)
    if not request.user.is_superuser:
        qs=qs.filter(company=request.user.company)
    return qs.get()

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
    permission_classes=[IsAuthenticated]
    def get(self,request,job_id):
        job=_job(request,job_id); state=_state(job); lines=[]
        for line in state["lines"]:
            item=StockItem.objects.filter(pk=line.get("partId"),company=job.company).first()
            lines.append(_line_state(line,item))
        return Response({"lines":lines,"purchaseOrders":state["purchaseOrders"]})

class JobIssuesView(APIView):
    permission_classes=[IsAuthenticated]
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
            StockMovement.objects.create(company=job.company,branch=job.branch,item=item,movement_type="job_issue",quantity=-qty,reference=job.job_number,job=job,created_by=request.user)
        _save(job,state); return Response(state)

class JobReturnsView(APIView):
    permission_classes=[IsAuthenticated]
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
                StockMovement.objects.create(company=job.company,branch=job.branch,item=item,movement_type="return",quantity=qty,reference=job.job_number,job=job,created_by=request.user)
            ret={"id":f"RET-{timezone.now().timestamp()}","returnNo":f"RET-{timezone.now().timestamp()}","qty":qty,"disposition":disposition,"returnedBy":request.user.name,"returnedAt":timezone.now().isoformat()}
            line.setdefault("returns",[]).insert(0,ret)
            state["transactions"].insert(0,{"id":ret["id"],"type":"Return","partName":line.get("partName"),"qty":qty,"reference":ret["returnNo"],"createdAt":ret["returnedAt"],"createdBy":request.user.name})
        _save(job,state); return Response(state)

class CompletionEligibilityView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request,job_id):
        job=_job(request,job_id); state=_state(job); blocking=[]
        for line in state["lines"]:
            if line.get("lineStatus")=="Cancelled": continue
            item=StockItem.objects.filter(pk=line.get("partId"),company=job.company).first()
            row=_line_state(line,item)
            if row["pendingQty"]>0: blocking.append({"lineId":line.get("id"),"partName":line.get("partName"),"pendingQty":row["pendingQty"],"reason":f"{line.get('partName')}: {row['pendingQty']} pending"})
        return Response({"can_complete":not blocking,"blocking_reasons":blocking})

class PartsTransactionsView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request,job_id):
        job=_job(request,job_id); return Response({"items":_state(job)["transactions"]})

class JobPurchaseOrderView(APIView):
    permission_classes=[IsAuthenticated]
    def post(self,request,job_id):
        job=_job(request,job_id); state=_state(job)
        line=next((x for x in state["lines"] if str(x.get("id"))==str(request.data.get("job_card_part_id"))),None)
        if not line: raise ValidationError("Part line not found.")
        po={"id":f"PO-{timezone.now().timestamp()}","poNo":f"PO-{timezone.now().timestamp()}","jobId":str(job.id),"lineId":line.get("id"),"partId":line.get("partId"),"partName":line.get("partName"),"vendor":request.data.get("vendor") or "Supplier","type":request.data.get("type") or "Cash","orderDate":timezone.localdate().isoformat(),"requestedQty":line.get("requestedQty") or 0,"orderedQty":float(request.data.get("ordered_qty") or 0),"unitCost":float(request.data.get("unit_cost") or 0),"discount":float(request.data.get("discount") or 0),"tax":float(request.data.get("tax") or 0),"receivedQty":0,"status":"Ordered"}
        state["purchaseOrders"].insert(0,po); _save(job,state); return Response(po,status=201)

class PurchaseOrderInwardView(APIView):
    permission_classes=[IsAuthenticated]
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
        return Response(po)
