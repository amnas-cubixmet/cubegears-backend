"""Company-scoped vehicle ownership and canonical registration validation."""

import re

from django.db.models import Value
from django.db.models.functions import Replace, Upper
from rest_framework import serializers

from apps.customers.models import Customer
from .models import Vehicle


def canonical_registration(value):
    """Normalize common case/spacing/hyphen variations without modifying old rows."""
    registration = re.sub(r"[\s-]+", "", str(value or "")).upper()
    if not re.fullmatch(r"[A-Z0-9]{4,30}", registration):
        raise serializers.ValidationError(
            "Enter a valid vehicle registration (letters and numbers, 4–30 characters)."
        )
    return registration


class VehicleSerializer(serializers.ModelSerializer):
    customerId = serializers.PrimaryKeyRelatedField(
        source="customer", queryset=Customer.objects.all(), required=False,
    )
    customerName = serializers.CharField(source="customer.name", read_only=True)
    customerStatus = serializers.CharField(source="customer.status", read_only=True)
    branchId = serializers.UUIDField(source="branch_id", read_only=True)
    branchName = serializers.CharField(source="branch.name", read_only=True)
    regNo = serializers.CharField(source="registration", required=False)
    licensePlate = serializers.CharField(source="registration", read_only=True)
    fuelType = serializers.CharField(source="fuel_type", required=False, allow_blank=True)
    engineNo = serializers.CharField(source="engine_no", required=False, allow_blank=True)
    kilometres = serializers.IntegerField(source="odometer", required=False, min_value=0)
    lastServiceDate = serializers.DateField(source="last_service_date", required=False, allow_null=True)
    nextServiceDue = serializers.DateField(source="next_service_due", required=False, allow_null=True)
    insuranceExpiry = serializers.DateField(source="insurance_expiry", required=False, allow_null=True)
    customer = serializers.PrimaryKeyRelatedField(
        queryset=Customer.objects.all(), write_only=True, required=False,
    )

    class Meta:
        model = Vehicle
        exclude = ("company", "branch")
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs):
        request = self.context.get("request")
        company_id = getattr(getattr(request, "user", None), "company_id", None)
        if not company_id:
            raise serializers.ValidationError({"customerId": "A workshop account is required."})

        customer = attrs.get("customer", self.instance.customer if self.instance else None)
        if not customer:
            raise serializers.ValidationError({"customerId": "Select an existing customer."})
        if customer.company_id != company_id:
            raise serializers.ValidationError({"customerId": "Customer belongs to another workshop."})
        if customer.status == "archived" and (
            not self.instance or customer.pk != self.instance.customer_id
        ):
            raise serializers.ValidationError({"customerId": "Restore this customer before adding a vehicle."})

        if self.instance and customer.pk != self.instance.customer_id:
            # Transferring an already serviced/billed vehicle would orphan
            # customer-level job/invoice history and portal access.
            if self.instance.jobs.exists() or self.instance.invoices.exists():
                raise serializers.ValidationError({
                    "customerId": "This vehicle has service or invoice history. Ownership cannot be changed."
                })

        if "registration" in attrs or not self.instance:
            raw = attrs.get("registration", self.instance.registration if self.instance else "")
            registration = canonical_registration(raw)
            # Handle pre-normalization historical records without destructive
            # migrations. Newly stored values use the existing DB unique key.
            comparable = Upper(Replace(Replace(
                "registration", Value(" "), Value(""),
            ), Value("-"), Value("")))
            matches = Vehicle.objects.filter(company_id=company_id).annotate(
                canonical_key=comparable,
            ).filter(canonical_key=registration)
            if self.instance:
                matches = matches.exclude(pk=self.instance.pk)
            match = matches.first()
            if match:
                raise serializers.ValidationError({
                    "registration": "A vehicle with this registration already exists. Open its profile instead."
                })
            attrs["registration"] = registration

        if not self.instance:
            branch = customer.branch or getattr(request.user, "branch", None)
            if branch and not branch.is_active:
                raise serializers.ValidationError({
                    "customerId": "Customer branch is inactive. Choose an active branch."
                })
        return attrs
