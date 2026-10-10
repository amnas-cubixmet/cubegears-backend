from rest_framework import serializers
from .models import Job,JobEstimate,JobPart,JobPhoto,JobActivity

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
    class Meta: model=JobPhoto; exclude=("company","branch")
class JobActivitySerializer(serializers.ModelSerializer):
    actorName=serializers.CharField(source="actor.name",read_only=True)
    class Meta: model=JobActivity; exclude=("company","branch")
class JobSerializer(serializers.ModelSerializer):
    partsWorkflow=serializers.JSONField(source="parts_workflow",required=False)
    customerId=serializers.UUIDField(source="customer_id",read_only=True)
    customerName=serializers.CharField(source="customer.name",read_only=True)
    customerPhone=serializers.CharField(source="customer.phone",read_only=True)
    vehicleId=serializers.UUIDField(source="vehicle_id",read_only=True)
    vehicleReg=serializers.CharField(source="vehicle.registration",read_only=True)
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
        read_only_fields=("id","created_at","updated_at","workflow_progress")
        extra_kwargs={"job_number": {"required": False}}
    def get_vehicleInfo(self,obj):
        return " ".join(filter(None,[obj.vehicle.make,obj.vehicle.model,obj.vehicle.variant]))

    def get_assignedEmployeeId(self,obj):
        if not obj.technician:
            return None
        try:
            return str(obj.technician.employee_profile.id)
        except Exception:
            return None
