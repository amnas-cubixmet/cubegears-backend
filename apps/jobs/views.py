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
