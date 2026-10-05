from rest_framework import serializers
from .models import Job,JobEstimate,JobPart,JobPhoto,JobActivity

class JobEstimateSerializer(serializers.ModelSerializer):
    class Meta: model=JobEstimate; exclude=("company","branch")
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
    vehicleId=serializers.UUIDField(source="vehicle_id",read_only=True)
    vehicleReg=serializers.CharField(source="vehicle.registration",read_only=True)
    vehicleInfo=serializers.SerializerMethodField()
    jobNumber=serializers.CharField(source="job_number",required=False)
    createdDate=serializers.DateTimeField(source="created_at",read_only=True)
    parts=JobPartSerializer(many=True,read_only=True)
    photos=JobPhotoSerializer(many=True,read_only=True)
    activities=JobActivitySerializer(many=True,read_only=True)
    estimates=JobEstimateSerializer(many=True,read_only=True)
    class Meta:
        model=Job
        exclude=("company","branch")
        read_only_fields=("id","created_at","updated_at")
    def get_vehicleInfo(self,obj):
        return " ".join(filter(None,[obj.vehicle.make,obj.vehicle.model,obj.vehicle.variant]))
