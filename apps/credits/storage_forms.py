"""Control Panel form for server-authoritative per-GB storage wallet rates."""
from decimal import Decimal

from django import forms

from .models import StorageCreditPolicy


class StorageCreditPolicyForm(forms.ModelForm):
    class Meta:
        model = StorageCreditPolicy
        fields = ["credits_per_gb", "included_gb", "billing_mode", "meter_interval_seconds", "billing_period", "is_active"]
        labels = {
            "credits_per_gb": "Credits per GB",
            "included_gb": "Free storage (GB) per company",
            "billing_period": "Billing frequency",
            "billing_mode": "Billing method",
            "meter_interval_seconds": "Meter interval (seconds)",
            "is_active": "Enable automatic storage billing",
        }
        widgets = {
            "credits_per_gb": forms.NumberInput(attrs={
                "class": "form-control", "min": "0", "max": "1000000", "step": "0.01",
                "placeholder": "Example: 10.00",
            }),
            "included_gb": forms.NumberInput(attrs={
                "class": "form-control", "min": "0", "max": "1000000", "step": "0.001",
                "placeholder": "Example: 1.000",
            }),
            "billing_period": forms.Select(attrs={"class": "form-control"}),
            "billing_mode": forms.Select(attrs={"class": "form-control"}),
            "meter_interval_seconds": forms.NumberInput(attrs={"class": "form-control", "min": "60", "max": "86400", "step": "60"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Older forms and admin tests may omit newly introduced fields.
        self.fields["billing_mode"].required = False
        self.fields["meter_interval_seconds"].required = False

    def clean(self):
        result = super().clean()
        if not result.get("billing_mode"):
            result["billing_mode"] = self.instance.billing_mode or "snapshot"
        if result.get("meter_interval_seconds") is None:
            result["meter_interval_seconds"] = self.instance.meter_interval_seconds or 3600
        price = result.get("credits_per_gb")
        free = result.get("included_gb")
        if price is not None and not Decimal("0") <= price <= Decimal("1000000"):
            self.add_error("credits_per_gb", "Enter a rate between 0 and 1,000,000 credits.")
        if free is not None and not Decimal("0") <= free <= Decimal("1000000"):
            self.add_error("included_gb", "Enter a free allowance between 0 and 1,000,000 GB.")
        if result.get("is_active") and price is not None and price <= 0:
            self.add_error("credits_per_gb", "Set a rate above zero before enabling billing.")
        interval = result.get("meter_interval_seconds")
        if interval is not None and not 60 <= interval <= 86400:
            self.add_error("meter_interval_seconds", "Choose a metering interval from 60 to 86400 seconds.")
        if result.get("billing_mode") == "metered_monthly" and result.get("billing_period") != "monthly":
            self.add_error("billing_period", "Metered usage settles monthly; select Monthly.")

        return result
