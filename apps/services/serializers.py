from rest_framework import serializers
from .models import Service, ServiceCategory


class ServiceCategorySerializer(serializers.ModelSerializer):
    parentId = serializers.PrimaryKeyRelatedField(
        source="parent", queryset=ServiceCategory.objects.all(),
        allow_null=True, required=False,
    )
    status = serializers.SerializerMethodField()

    class Meta:
        model = ServiceCategory
        exclude = ("company", "branch", "parent")

    def get_status(self, obj):
        return "active" if obj.is_active else "inactive"

    def validate(self, attrs):
        instance = self.instance
        parent = attrs.get("parent", instance.parent if instance else None)
        if parent:
            company = self.context["request"].user.company
            if parent.company_id != company.pk:
                raise serializers.ValidationError({"parentId": "Parent belongs to another workshop."})
            seen = {instance.pk} if instance else set()
            node = parent
            while node:
                if node.pk in seen:
                    raise serializers.ValidationError({"parentId": "Category hierarchy cannot contain a cycle."})
                seen.add(node.pk)
                node = node.parent
        return attrs


class ServiceSerializer(serializers.ModelSerializer):
    categoryName = serializers.CharField(source="category.name", read_only=True, default="")
    categoryId = serializers.PrimaryKeyRelatedField(
        source="category", queryset=ServiceCategory.objects.all(),
        required=False, allow_null=True,
    )
    hsnSac = serializers.CharField(source="hsn_sac", required=False, allow_blank=True)
    taxRate = serializers.DecimalField(
        source="tax_rate", max_digits=5, decimal_places=2, required=False,
    )
    pricingType = serializers.ChoiceField(
        source="pricing_type", choices=("Fixed", "Starting", "Inspection Based"),
        required=False,
    )
    durationMinutes = serializers.SerializerMethodField()
    taxTreatment = serializers.ChoiceField(
        source="tax_treatment", choices=(
            "taxable", "exempt", "nil_rated", "non_gst", "out_of_scope",
        ), required=False,
    )

    class Meta:
        model = Service
        exclude = ("company", "branch", "category", "hsn_sac", "tax_rate", "tax_treatment", "pricing_type")

    def get_durationMinutes(self, obj):
        return int(obj.labour_hours * 60)

    def validate(self, attrs):
        category = attrs.get("category", getattr(self.instance, "category", None))
        if category and category.company_id != self.context["request"].user.company_id:
            raise serializers.ValidationError({"categoryId": "Category belongs to another workshop."})
        return attrs
