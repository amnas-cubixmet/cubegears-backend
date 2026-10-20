"""Control Panel form for server-authoritative per-GB storage wallet rates."""
from decimal import Decimal

from django import forms
from django.conf import settings

from .models import StorageCreditPolicy, StorageMonitoringPolicy


class StorageCreditPolicyForm(forms.ModelForm):
    # Keep the existing seconds column for Celery/backwards compatibility.
    # The management UI accepts hours and converts on the server.
    meter_interval_hours = forms.DecimalField(
        max_digits=8, decimal_places=6, min_value=Decimal("0.016667"),
        max_value=Decimal("24"), required=False, label="Meter interval (hours)",
        widget=forms.NumberInput(attrs={
            "class": "form-control", "min": "0.016667", "max": "24",
            "step": "any", "placeholder": "1",
        }),
    )

    class Meta:
        model = StorageCreditPolicy
        fields = ["credits_per_gb", "included_gb", "billing_mode", "billing_period", "is_active"]
        labels = {
            "credits_per_gb": "Credits per GB",
            "included_gb": "Free storage (GB) per company",
            "billing_period": "Billing frequency",
            "billing_mode": "Billing method",
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
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if settings.STORAGE_MONTHLY_METERED_ENABLED:
            self.fields['billing_mode'].choices = [('snapshot', 'Time-weighted monthly usage (active engine)')]
        # Older forms and admin tests may omit newly introduced fields.
        self.fields["billing_mode"].required = False
        if not self.is_bound:
            seconds = self.instance.meter_interval_seconds or 3600
            self.initial["meter_interval_hours"] = (Decimal(seconds) / Decimal(3600)).quantize(Decimal("0.000001")).normalize()

    def clean(self):
        result = super().clean()
        if not result.get("billing_mode"):
            result["billing_mode"] = self.instance.billing_mode or "snapshot"
        hours = result.get("meter_interval_hours")
        # Older clients posting the seconds field still work. Never silently
        # rewrite an existing metering cadence when an old form omits hours.
        if hours is None:
            submitted_seconds = self.data.get("meter_interval_seconds") if self.is_bound else None
            try:
                interval = int(submitted_seconds) if submitted_seconds is not None else (self.instance.meter_interval_seconds or 3600)
            except (ValueError, TypeError):
                self.add_error("meter_interval_hours", "Enter a valid number of hours.")
                interval = self.instance.meter_interval_seconds or 3600
        else:
            interval = int((hours * Decimal("3600")).to_integral_value())
        result["meter_interval_seconds"] = interval
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
            self.add_error("meter_interval_hours", "Choose an interval between 1 minute and 24 hours.")
        if result.get("billing_mode") == "metered_monthly" and result.get("billing_period") != "monthly":
            self.add_error("billing_period", "Metered usage settles monthly; select Monthly.")

        if settings.STORAGE_MONTHLY_METERED_ENABLED and result.get('is_active'):
            if result.get('billing_mode') != 'snapshot' or result.get('billing_period') != 'monthly':
                self.add_error('billing_mode', 'The monthly storage meter requires monthly usage mode. Do not combine two storage engines.')

        return result

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.meter_interval_seconds = self.cleaned_data["meter_interval_seconds"]
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class StorageMonitoringPolicyForm(forms.ModelForm):
    """Configure read-only storage snapshots, not any wallet debit."""

    INTERVAL_CHOICES = (
        (3600, "Every 1 hour"),
        (7200, "Every 2 hours"),
        (21600, "Every 6 hours"),
        (43200, "Every 12 hours"),
        (86400, "Every 24 hours"),
    )
    interval_seconds = forms.TypedChoiceField(
        choices=INTERVAL_CHOICES, coerce=int,
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    class Meta:
        model = StorageMonitoringPolicy
        fields = ["enabled", "interval_seconds"]
        # Other settings forms also expose an `enabled` field on /settings/.
        # Keep the existing POST parameter name but give this switch a unique
        # DOM ID so clicking its label never toggles a different billing policy.
        widgets = {
            "enabled": forms.CheckboxInput(attrs={"id": "id_storage_monitoring_enabled"}),
        }
