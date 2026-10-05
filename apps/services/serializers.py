from rest_framework import serializers
from .models import Service,ServiceCategory
class ServiceCategorySerializer(serializers.ModelSerializer):
    class Meta: model=ServiceCategory; exclude=("company","branch")
class ServiceSerializer(serializers.ModelSerializer):
    categoryName=serializers.CharField(source="category.name",read_only=True)
    class Meta: model=Service; exclude=("company","branch")
