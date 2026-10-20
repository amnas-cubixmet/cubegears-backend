from django import forms
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import BillingCyclePolicy


class BillingCyclePolicyForm(forms.ModelForm):
    due_day = forms.IntegerField(
        min_value=1, max_value=28, label="Payment start day (1–28)",
        help_text="Start automatic collection on this day of the following month.",
        widget=forms.NumberInput(attrs={"min": 1, "max": 28, "inputmode": "numeric"}),
    )
    due_end_day = forms.IntegerField(
        min_value=1, max_value=28, label="Payment end day (1–28)",
        help_text="Last grace day; unpaid accounts may be restricted starting the next day.",
        widget=forms.NumberInput(attrs={"min": 1, "max": 28, "inputmode": "numeric"}),
    )

    class Meta:
        model = BillingCyclePolicy
        fields = ["enabled", "due_day", "due_end_day", "due_time", "calculation_interval_seconds",
                  "storage_tracking_interval_seconds", "rate_duration_seconds",
                  "event_tracking_enabled", "restrict_overdue", "allow_zero_balance_usage"]
        widgets = {"due_time": forms.TimeInput(format="%H:%M", attrs={"type": "time"})}
        help_texts = {
            "due_time": "Local time in the server billing timezone. Celery checks every minute.",
            "calculation_interval_seconds": "Feature calculation interval: 60 to 86400 seconds.",
            "storage_tracking_interval_seconds": "Storage accrual interval: 60 to 86400 seconds. Event mode reads the cached byte total, not every file.",
            "rate_duration_seconds": "0 = actual calendar month. Otherwise the configured credits apply per this many seconds (minimum 60). Statements still close monthly.",
            "event_tracking_enabled": "Track file size and active staff/account changes at the time of each application save/delete. Existing files are counted once.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("due_time", "calculation_interval_seconds", "storage_tracking_interval_seconds", "rate_duration_seconds"):
            self.fields[name].required = False

    def clean_due_time(self):
        return (self.cleaned_data.get("due_time") or self.instance.due_time).replace(second=0, microsecond=0)

    def clean_calculation_interval_seconds(self):
        return self._interval("calculation_interval_seconds")

    def clean_storage_tracking_interval_seconds(self):
        return self._interval("storage_tracking_interval_seconds")

    def _interval(self, name):
        value = self.cleaned_data.get(name)
        if value is None:
            value = getattr(self.instance, name)
        if not 60 <= value <= 86400:
            raise forms.ValidationError("Choose an interval from 60 to 86400 seconds.")
        return value

    def clean_rate_duration_seconds(self):
        value = self.cleaned_data.get("rate_duration_seconds")
        if value is None:
            value = self.instance.rate_duration_seconds
        if value != 0 and not 60 <= value <= 366 * 86400:
            raise forms.ValidationError("Use 0 for a calendar month or 60 to 31622400 seconds.")
        return value

    def clean(self):
        result = super().clean()
        start_day, end_day = result.get("due_day"), result.get("due_end_day")
        if start_day is not None and end_day is not None and end_day < start_day:
            self.add_error("due_end_day", "Payment end day must be on or after the start day.")
        if result.get("enabled"):
            # The 5th-to-5th engine must never run alongside calendar billing.
            from .cycle_billing import effective_policy
            from .models import MonthlyBillingPolicy
            if effective_policy():
                raise forms.ValidationError(
                    "Disable the old 5th-to-5th billing cycle before enabling calendar-month billing."
                )
            if MonthlyBillingPolicy.objects.filter(enabled=True).exists():
                raise forms.ValidationError(
                    "Disable legacy monthly collection policy before enabling calendar-month billing."
                )
            if not (
                settings.STORAGE_MONTHLY_METERED_ENABLED
                or (settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED
                    and settings.CREDIT_FEATURE_ENFORCEMENT_ENABLED
                    and not settings.CREDIT_LABEL_ONLY_ACCESS_ENABLED)
            ):
                raise forms.ValidationError(
                    "Enable the time-weighted monthly storage and/or feature meter in backend settings first."
                )
            if not settings.CREDIT_BILLING_CELERY_ENABLED:
                raise forms.ValidationError(
                    "Scheduled credit billing must be enabled, or use the explicit billing management command."
                )
            if settings.CREDIT_POSTPAID_FEATURE_BILLING_ENABLED:
                raise forms.ValidationError("Disable the legacy feature session billing engine first.")
            from .models import StorageCreditPolicy
            if StorageCreditPolicy.objects.filter(is_active=True, billing_mode="metered_monthly").exists():
                raise forms.ValidationError("Disable the legacy storage usage-interval engine first.")
        return result

    @transaction.atomic
    def save(self, commit=True):
        policy = super().save(commit=False)
        if policy.enabled and not policy.effective_month:
            # First enabled calendar month is the first billable one. Existing
            # historical periods are NOT auto-debited or retroactively priced.
            policy.effective_month = timezone.localdate().strftime("%Y-%m")
        if commit:
            from .runtime_billing import checkpoint_meters
            old = BillingCyclePolicy.objects.filter(pk=policy.pk).first() if policy.pk else None
            at = timezone.now()
            if old and old.enabled:
                checkpoint_meters(at=at)
            if policy.event_tracking_enabled and (not old or not old.event_tracking_enabled or not old.enabled):
                from .models import CompanyStorageState
                CompanyStorageState.objects.all().delete()
            policy.save()
            if policy.enabled:
                if not old or not old.enabled:
                    from .models import StorageMeterCursor, CompanyPostpaidFeature
                    StorageMeterCursor.objects.all().update(sampled_at=at)
                    CompanyPostpaidFeature.objects.filter(enabled=True).update(sampled_at=at)
                checkpoint_meters(at=at)
        return policy
