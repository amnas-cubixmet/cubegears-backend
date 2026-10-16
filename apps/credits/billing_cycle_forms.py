from django import forms
from django.conf import settings
from django.utils import timezone
from .models import BillingCyclePolicy


class BillingCyclePolicyForm(forms.ModelForm):
    due_day = forms.IntegerField(min_value=1, max_value=28, help_text="Deduct credits on this day of the following month.")

    class Meta:
        model = BillingCyclePolicy
        fields = ["enabled", "due_day", "restrict_overdue", "allow_zero_balance_usage"]

    def clean(self):
        result = super().clean()
        if result.get("enabled"):
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

    def save(self, commit=True):
        policy = super().save(commit=False)
        if policy.enabled and not policy.effective_month:
            # First enabled calendar month is the first billable one. Existing
            # historical periods are NOT auto-debited or retroactively priced.
            policy.effective_month = timezone.localdate().strftime("%Y-%m")
        if commit:
            policy.save()
        return policy
