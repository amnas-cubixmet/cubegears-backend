from rest_framework import serializers
from .models import Subscription,StorageUsage,DocumentTemplate,CompanySetting,SecurityEvent
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
