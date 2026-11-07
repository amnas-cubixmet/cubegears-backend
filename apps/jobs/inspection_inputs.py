"""Validated inspection records stored on the existing Job.inspection JSON master.

Only server-controlled IDs/timestamps/approval flags are persisted as metadata.
Real image bytes belong in the private VehicleInspectionPhoto storage system.
"""

from decimal import Decimal
from rest_framework import serializers


class FindingInputSerializer(serializers.Serializer):
    description = serializers.CharField(max_length=2000, trim_whitespace=True)
    title = serializers.CharField(max_length=240, required=False, allow_blank=True)
    category = serializers.CharField(max_length=100, required=False, allow_blank=True)
    severity = serializers.ChoiceField(choices=("Low", "Medium", "High", "Critical"), default="Medium")
    recommendedAction = serializers.CharField(max_length=1200, required=False, allow_blank=True)
    technicianFinding = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    requiredParts = serializers.CharField(max_length=1200, required=False, allow_blank=True)
    estimatedLabourTime = serializers.CharField(max_length=80, required=False, allow_blank=True)
    estimatedLabourCharge = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0"),
        max_value=Decimal("9999999999.99"), required=False,
    )
    estimatedPartCharge = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0"),
        max_value=Decimal("9999999999.99"), required=False,
    )
    estimatedCost = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0"),
        max_value=Decimal("9999999999.99"), required=False, allow_null=True,
    )
    complaintId = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    notes = serializers.CharField(max_length=2000, required=False, allow_blank=True)

    def validate_description(self, value):
        if len(value.strip()) < 3:
            raise serializers.ValidationError("Describe the issue (at least 3 characters).")
        return value.strip()


class DiagnosticInputSerializer(serializers.Serializer):
    code = serializers.RegexField(
        regex=r"^[A-Za-z0-9:_-]{2,24}$", max_length=24,
        error_messages={"invalid": "Enter a valid fault code using letters and numbers."},
    )
    description = serializers.CharField(max_length=500, allow_blank=True, required=False)
    system = serializers.CharField(max_length=100, allow_blank=True, required=False)
    status = serializers.ChoiceField(
        choices=("Active", "Pending", "Stored", "Cleared"), required=False,
    )

    def validate_code(self, value):
        return value.strip().upper()


def json_finding(values):
    """JSONField must not receive non-serializable Decimal objects."""
    output = {}
    for key, value in values.items():
        output[key] = str(value) if isinstance(value, Decimal) else value
    return output


def validate_finding(data):
    form = FindingInputSerializer(data=data)
    form.is_valid(raise_exception=True)
    return json_finding(form.validated_data)


def validate_diagnostic(data):
    form = DiagnosticInputSerializer(data=data)
    form.is_valid(raise_exception=True)
    return dict(form.validated_data)
