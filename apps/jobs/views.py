from django.db import transaction
from django.utils import timezone
from rest_framework import decorators,response,status
from rest_framework.exceptions import ValidationError
from common.viewsets import CompanyScopedModelViewSet
from .models import Job,JobActivity,JobEstimate,JobPart,JobPhoto
from .serializers import JobSerializer,JobEstimateSerializer,JobPartSerializer,JobPhotoSerializer,JobActivitySerializer

FLOW=[Job.STATUS_NEW,Job.STATUS_INSPECTION,Job.STATUS_ESTIMATE_PENDING,Job.STATUS_APPROVED,Job.STATUS_IN_PROGRESS,Job.STATUS_WAITING_PARTS,Job.STATUS_QC,Job.STATUS_READY,Job.STATUS_DELIVERED]

class JobViewSet(CompanyScopedModelViewSet):
    queryset=Job.objects.select_related("customer","vehicle","advisor","technician").prefetch_related("parts","photos","activities","estimates")
    serializer_class=JobSerializer
    def get_queryset(self):
        qs=super().get_queryset(); st=self.request.query_params.get("status")
        return qs.filter(status=st) if st else qs
    def perform_create(self,serializer):
        company=self.request.user.company
        count=Job.objects.filter(company=company).count()+1
        number=serializer.validated_data.get("job_number") or f"JOB-{timezone.now().year}-{count:05d}"
        job=serializer.save(company=company,branch=self.request.user.branch,job_number=number,advisor=self.request.user)
        JobActivity.objects.create(company=company,branch=self.request.user.branch,job=job,event="Job Created",description=f"{number} created",actor=self.request.user,to_status=job.status)

    @decorators.action(detail=True,methods=["patch"])
    def status(self,request,pk=None):
        job=self.get_object(); new_status=request.data.get("status")
        if new_status not in FLOW: raise ValidationError({"status":"Invalid job status."})
        old=job.status
        if new_status==Job.STATUS_DELIVERED: job.delivered_at=timezone.now()
        job.status=new_status; job.save(update_fields=["status","delivered_at","updated_at"])
        JobActivity.objects.create(company=job.company,branch=job.branch,job=job,event="Status Changed",description=f"{old} → {new_status}",actor=request.user,from_status=old,to_status=new_status)
        return response.Response(JobSerializer(job).data)

    @decorators.action(detail=True,methods=["get"],url_path="inspection")
    def inspection_detail(self,request,pk=None):
        job=self.get_object()
        return response.Response(job.inspection or {
            "status":"Not Started","checklist":{},"findings":[],
            "diagnosticScan":{"performed":False,"codes":[]},"photos":[],
            "summary":{"totalChecks":0,"good":0,"needsAttention":0,"issuesFound":0,"criticalIssues":0,"recommendedRepairs":0}
        })

    @decorators.action(detail=True,methods=["post"],url_path="inspection/start")
    def inspection_start(self,request,pk=None):
        job=self.get_object(); data=dict(job.inspection or {})
        data.setdefault("checklist",{}); data.setdefault("findings",[]); data.setdefault("diagnosticScan",{"performed":False,"codes":[]}); data.setdefault("photos",[])
        data["status"]="In Progress"; data["startedAt"]=timezone.now().isoformat()
        job.inspection=data; job.status=Job.STATUS_INSPECTION; job.save(update_fields=["inspection","status","updated_at"])
        return response.Response(data)

    @decorators.action(detail=True,methods=["patch"],url_path="inspection/checklist")
    def inspection_checklist(self,request,pk=None):
        job=self.get_object(); data=dict(job.inspection or {}); checklist=dict(data.get("checklist") or {})
        checklist[request.data.get("itemName","")]=request.data.get("status","Not Checked")
        data["checklist"]=checklist; job.inspection=data; job.save(update_fields=["inspection","updated_at"])
        return response.Response(data)

    @decorators.action(detail=True,methods=["post"],url_path="inspection/findings")
    def inspection_findings(self,request,pk=None):
        import uuid
        job=self.get_object(); data=dict(job.inspection or {}); findings=list(data.get("findings") or [])
        finding={"id":f"FND-{uuid.uuid4().hex[:10].upper()}","dateTime":timezone.now().isoformat(),"addedToEstimate":False,"estimateStatus":"Not Added",**request.data}
        findings.insert(0,finding); data["findings"]=findings; job.inspection=data; job.save(update_fields=["inspection","updated_at"])
        return response.Response(finding,status=status.HTTP_201_CREATED)

    @decorators.action(detail=True,methods=["put","delete"],url_path=r"inspection/findings/(?P<finding_id>[^/.]+)")
    def inspection_finding_detail(self,request,pk=None,finding_id=None):
        job=self.get_object(); data=dict(job.inspection or {}); findings=list(data.get("findings") or [])
        idx=next((i for i,x in enumerate(findings) if str(x.get("id"))==str(finding_id)),None)
        if idx is None: return response.Response({"message":"Finding not found."},status=404)
        if request.method=="DELETE":
            removed=findings.pop(idx); data["findings"]=findings; job.inspection=data; job.save(update_fields=["inspection","updated_at"])
            return response.Response(removed)
        findings[idx]={**findings[idx],**request.data}; data["findings"]=findings; job.inspection=data; job.save(update_fields=["inspection","updated_at"])
        return response.Response(findings[idx])

    @decorators.action(detail=True,methods=["post"],url_path="inspection/diagnostic")
    def inspection_diagnostic(self,request,pk=None):
        import uuid
        job=self.get_object(); data=dict(job.inspection or {}); scan=dict(data.get("diagnosticScan") or {})
        codes=list(scan.get("codes") or []); code={"id":f"OBD-{uuid.uuid4().hex[:8].upper()}",**request.data}
        codes.append(code); scan.update({"performed":True,"scanTime":timezone.now().isoformat(),"codes":codes}); data["diagnosticScan"]=scan
        job.inspection=data; job.save(update_fields=["inspection","updated_at"]); return response.Response(code,status=201)

    @decorators.action(detail=True,methods=["post"],url_path="inspection/photos")
    def inspection_photo(self,request,pk=None):
        import uuid
        job=self.get_object(); data=dict(job.inspection or {}); photos=list(data.get("photos") or [])
        photo={"id":f"PH-{uuid.uuid4().hex[:8].upper()}","dateTime":timezone.now().isoformat(),**request.data}
        photos.append(photo); data["photos"]=photos; job.inspection=data; job.save(update_fields=["inspection","updated_at"])
        return response.Response(photo,status=201)

    @decorators.action(detail=True,methods=["post"],url_path="inspection/complete")
    def inspection_complete(self,request,pk=None):
        job=self.get_object(); data=dict(job.inspection or {}); data["status"]="Completed"; data["completedAt"]=timezone.now().isoformat()
        job.inspection=data; job.status=Job.STATUS_ESTIMATE_PENDING if request.data.get("needsApproval",True) else job.status
        job.save(update_fields=["inspection","status","updated_at"])
        return response.Response({"inspection":data,"newJobStatus":job.status})

    @decorators.action(detail=True,methods=["post"],url_path=r"inspection/findings/(?P<finding_id>[^/.]+)/add-to-estimate")
    def inspection_add_to_estimate(self,request,pk=None,finding_id=None):
        job=self.get_object(); data=dict(job.inspection or {}); findings=list(data.get("findings") or [])
        finding=next((x for x in findings if str(x.get("id"))==str(finding_id)),None)
        if not finding: return response.Response({"message":"Finding not found."},status=404)
        finding["addedToEstimate"]=True; finding["estimateStatus"]="Added to Estimate"; job.inspection={**data,"findings":findings}
        job.save(update_fields=["inspection","updated_at"]); return response.Response(finding)

    @decorators.action(detail=True,methods=["get","post"])
    def estimates(self,request,pk=None):
        job=self.get_object()
        if request.method=="POST":
            s=JobEstimateSerializer(data=request.data); s.is_valid(raise_exception=True)
            obj=s.save(company=job.company,branch=job.branch,job=job)
            return response.Response(JobEstimateSerializer(obj).data,status=status.HTTP_201_CREATED)
        return response.Response(JobEstimateSerializer(job.estimates.all(),many=True).data)

    @decorators.action(detail=True,methods=["get","post"])
    def photos(self,request,pk=None):
        job=self.get_object()
        if request.method=="POST":
            s=JobPhotoSerializer(data=request.data); s.is_valid(raise_exception=True)
            obj=s.save(company=job.company,branch=job.branch,job=job,uploaded_by=request.user)
            return response.Response(JobPhotoSerializer(obj).data,status=status.HTTP_201_CREATED)
        return response.Response(JobPhotoSerializer(job.photos.all(),many=True).data)

    @decorators.action(detail=True,methods=["get"])
    def activity(self,request,pk=None):
        return response.Response(JobActivitySerializer(self.get_object().activities.all(),many=True).data)

    @decorators.action(detail=True,methods=["post"],url_path="issue-part")
    @transaction.atomic
    def issue_part(self,request,pk=None):
        from apps.inventory.models import StockItem,StockMovement
        job=self.get_object(); item=StockItem.objects.select_for_update().get(pk=request.data.get("item"),company=job.company)
        qty=float(request.data.get("quantity",0))
        if qty<=0 or float(item.available_quantity)<qty: raise ValidationError("Insufficient stock.")
        item.on_hand=float(item.on_hand)-qty; item.save(update_fields=["on_hand","updated_at"])
        part=JobPart.objects.create(company=job.company,branch=job.branch,job=job,item=item,quantity=qty,unit_price=item.selling_price,issued=True)
        StockMovement.objects.create(company=job.company,branch=job.branch,item=item,movement_type="job_issue",quantity=-qty,reference=job.job_number,job=job,created_by=request.user)
        return response.Response(JobPartSerializer(part).data,status=status.HTTP_201_CREATED)
