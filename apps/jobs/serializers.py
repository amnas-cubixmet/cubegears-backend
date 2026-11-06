from rest_framework import serializers
from .models import Job,JobEstimate,JobPart,JobPhoto,JobActivity
from .complaint_status import complaint_status_for_job, normalize_complaints
from .india_gst import state_code
from apps.invoices.gst_rate_options import gst_rate_options

class JobEstimateSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobEstimate
        exclude = ("company", "branch")
        read_only_fields = ("id", "job", "created_at", "updated_at")
class JobPartSerializer(serializers.ModelSerializer):
    itemName=serializers.CharField(source="item.name",read_only=True)
    sku=serializers.CharField(source="item.sku",read_only=True)
    class Meta: model=JobPart; exclude=("company","branch")
class JobPhotoSerializer(serializers.ModelSerializer):
    hasUpload=serializers.SerializerMethodField()

    def get_hasUpload(self, obj):
        return bool(obj.file)

    class Meta:
        model=JobPhoto
        exclude=("company", "branch", "file")
        read_only_fields=("id", "job", "uploaded_by", "created_at", "updated_at")
class JobActivitySerializer(serializers.ModelSerializer):
    actorName=serializers.CharField(source="actor.name",read_only=True)
    class Meta: model=JobActivity; exclude=("company","branch")
class JobSerializer(serializers.ModelSerializer):
    gstInfo=serializers.SerializerMethodField()
    delivery=serializers.SerializerMethodField()

    def get_delivery(self, obj):
        return (obj.workflow_progress or {}).get("delivery") or {}

    def get_gstInfo(self, obj):
        gstin = str(obj.company.gstin or "").strip().upper()
        supplier = state_code(gstin[:2] if len(gstin) == 15 else "") or state_code(
            getattr(obj.branch, "state", "") or obj.company.state
        )
        recipient = state_code(obj.customer.state or "")
        return {
            "registered": bool(gstin),
            "supplierState": supplier,
            "placeOfSupply": recipient or supplier,
            "customerGstin": obj.customer.gstin or "",
            "gstRateOptions": gst_rate_options(),
        }

    partsWorkflow=serializers.JSONField(source="parts_workflow",read_only=True)
    workUpdates=serializers.JSONField(source="work_updates",required=False)
    branchId=serializers.UUIDField(source="branch_id",read_only=True,allow_null=True)
    branchName=serializers.CharField(source="branch.name",read_only=True,default="")
    customerId=serializers.UUIDField(source="customer_id",read_only=True)
    customerName=serializers.CharField(source="customer.name",read_only=True)
    customerPhone=serializers.CharField(source="customer.phone",read_only=True)
    customerWhatsapp=serializers.CharField(source="customer.whatsapp",read_only=True)
    vehicleId=serializers.UUIDField(source="vehicle_id",read_only=True)
    vehicleReg=serializers.CharField(source="vehicle.registration",read_only=True)
    vin=serializers.CharField(source="vehicle.vin",read_only=True)
    kilometre=serializers.IntegerField(source="odometer",read_only=True)
    fuelLevel=serializers.CharField(source="fuel_level",read_only=True)
    vehicleInfo=serializers.SerializerMethodField()
    jobNumber=serializers.CharField(source="job_number",required=False)
    assignedEmployeeId=serializers.SerializerMethodField()
    assignedEmployeeName=serializers.CharField(source="technician.name",read_only=True)
    technicianName=serializers.CharField(source="technician.name",read_only=True)
    customerRating=serializers.IntegerField(source="customer_rating",required=False,allow_null=True,min_value=1,max_value=5)
    customerFeedback=serializers.CharField(source="customer_feedback",required=False,allow_blank=True)
    createdDate=serializers.DateTimeField(source="created_at",read_only=True)
    parts=JobPartSerializer(many=True,read_only=True)
    photos=JobPhotoSerializer(many=True,read_only=True)
    activities=JobActivitySerializer(many=True,read_only=True)
    estimates=JobEstimateSerializer(many=True,read_only=True)
    class Meta:
        model=Job
        exclude=("company","branch")
        read_only_fields=("id","created_at","updated_at","workflow_progress","parts_workflow")
        extra_kwargs={"job_number": {"required": False}}
    def validate_workUpdates(self, value):
        if not isinstance(value, list) or len(value) > 500:
            raise serializers.ValidationError("Updates must be a list of at most 500 records.")
        for record in value:
            if not isinstance(record, dict) or len(str(record.get("note") or "")) > 2000:
                raise serializers.ValidationError("Each update needs a valid note (maximum 2000 characters).")
        return value

    def validate_complaints(self, value):
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise serializers.ValidationError("Complaints must be a list of complaint records.")
        if len(value) > 200:
            raise serializers.ValidationError("Maximum 200 complaints per Job Card.")
        previous = list(self.instance.complaints or []) if self.instance else []
        by_id = {str(row.get("id")): row for row in previous
                 if isinstance(row, dict) and row.get("id") is not None}
        result = []
        for index, row in enumerate(value):
            stored = by_id.get(str(row.get("id"))) if row.get("id") is not None else (
                previous[index] if index < len(previous) and
                isinstance(previous[index], dict) and previous[index].get("id") is None
                else None
            )
            if stored:
                # Generic Job PATCH may edit wording, but must not forge a
                # technician's status, timestamp, evidence, or audit history.
                protected = ("status", "statusHistory", "statusUpdatedAt",
                             "statusUpdatedBy", "resolutionNotes", "workReference")
                row = {**row, **{key: stored[key] for key in protected if key in stored}}
                for key in protected:
                    if key not in stored:
                        row.pop(key, None)
            else:
                row = {**row, "status": "Pending"}
                for key in ("statusHistory", "statusUpdatedAt", "statusUpdatedBy",
                            "resolutionNotes", "workReference"):
                    row.pop(key, None)
            result.append(row)
        return normalize_complaints(result)

    def to_representation(self, instance):
        result = super().to_representation(instance)
        result["complaints"] = normalize_complaints(
            instance.complaints, complaint_status_for_job(instance)
        )
        return result

    def get_vehicleInfo(self,obj):
        return " ".join(filter(None,[obj.vehicle.make,obj.vehicle.model,obj.vehicle.variant]))

    def get_assignedEmployeeId(self,obj):
        if not obj.technician:
            return None
        try:
            return str(obj.technician.employee_profile.id)
        except Exception:
            return None
