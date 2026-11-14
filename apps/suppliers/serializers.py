"""Canonical supplier CRUD serializer; inventory imports this for legacy APIs."""
from rest_framework import serializers
from .models import Supplier
from common.phone_numbers import normalize_contact_phone
from .gstin import normalized_supplier_gstin, supplier_gstin_error

class SupplierSerializer(serializers.ModelSerializer):
    gstNo=serializers.CharField(source="gstin",required=False,allow_blank=True)
    class Meta:
        model = Supplier
        exclude = ("company", "branch", "gstin")
        read_only_fields = ("code",)

    def validate(self, attrs):
        request = self.context.get("request")
        company_id = getattr(getattr(request, "user", None), "company_id", None)
        if not company_id:
            return attrs
        name = (attrs.get("name", getattr(self.instance, "name", "")) or "").strip()
        phone = attrs.get("phone", getattr(self.instance, "phone", "")) or ""
        # Do not reject untouched legacy records when updating another field.
        # Explicit new or changed phone values must follow national lengths.
        if "phone" in attrs:
            phone = normalize_contact_phone(str(phone), required=False)
        else:
            phone = str(phone).strip()
        gstin = normalized_supplier_gstin(
            attrs.get("gstin", getattr(self.instance, "gstin", ""))
        )
        # Keep unchanged legacy supplier records editable. If a GSTIN is
        # entered/changed, validate before saving instead of breaking purchases.
        if "gstin" in attrs:
            gstin_problem = supplier_gstin_error(gstin)
            if gstin_problem:
                raise serializers.ValidationError({"gstNo": gstin_problem})
        if not name:
            raise serializers.ValidationError({"name": "Supplier Name is required."})
        qs = Supplier.objects.filter(company_id=company_id)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if gstin and qs.filter(gstin__iexact=gstin).exists():
            raise serializers.ValidationError({"gstNo": "A supplier with this GSTIN already exists."})
        if phone and qs.filter(name__iexact=name, phone=phone).exists():
            raise serializers.ValidationError({"name": "A supplier with this name and phone already exists."})
        attrs["name"] = name
        if "phone" in attrs:
            attrs["phone"] = phone
        if "gstin" in attrs:
            attrs["gstin"] = gstin
        return attrs
