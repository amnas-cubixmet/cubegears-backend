from rest_framework import serializers
from .models import Subscription,StorageUsage,DocumentTemplate,CompanySetting,SecurityEvent,MediaFile
class SubscriptionSerializer(serializers.ModelSerializer):
    class Meta: model=Subscription; exclude=("company","branch")
class StorageUsageSerializer(serializers.ModelSerializer):
    class Meta: model=StorageUsage; exclude=("company","branch")
class DocumentTemplateSerializer(serializers.ModelSerializer):
    class Meta: model=DocumentTemplate; exclude=("company","branch")
class CompanySettingSerializer(serializers.ModelSerializer):
    class Meta: model=CompanySetting; exclude=("company","branch")
class SecurityEventSerializer(serializers.ModelSerializer):
    userName=serializers.CharField(source="user.name",read_only=True)
    class Meta: model=SecurityEvent; exclude=("company","branch")

class MediaFileSerializer(serializers.ModelSerializer):
    uploadedBy=serializers.CharField(source="uploaded_by.name",read_only=True)
    uploadedAt=serializers.DateTimeField(source="created_at",read_only=True)
    sizeMb=serializers.SerializerMethodField()
    type=serializers.CharField(source="mime_type",read_only=True)
    url=serializers.SerializerMethodField()
    class Meta:
        model=MediaFile
        fields=("id","name","category","sizeMb","uploadedBy","uploadedAt","type","url")
    def get_sizeMb(self,obj): return round(obj.size_bytes/(1024*1024),2)
    def get_url(self,obj):
        request=self.context.get("request")
        if not obj.file: return ""
        return request.build_absolute_uri(obj.file.url) if request else obj.file.url
