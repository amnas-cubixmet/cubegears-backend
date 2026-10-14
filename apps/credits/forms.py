from decimal import Decimal
from django import forms
from apps.companies.models import Company
from apps.saas.models import PlanFeature
from .models import CreditPackage, FeatureCreditPrice


class CreditPackageForm(forms.ModelForm):
    class Meta:
        model = CreditPackage
        fields = ["name", "credits", "price_inr", "is_active", "sort_order"]

    def clean(self):
        data = super().clean()
        qty, value = data.get("credits"), data.get("price_inr")
        if qty is not None and not Decimal("0") < qty <= Decimal("100000000"):
            self.add_error("credits", "Choose a positive credit amount.")
        if value is not None and not Decimal("1.00") <= value <= Decimal("10000000"):
            self.add_error("price_inr", "Razorpay packages must cost at least INR 1.")
        return data


class FeatureCreditPriceForm(forms.ModelForm):
    class Meta:
        model = FeatureCreditPrice
        fields = ["feature", "base_credits", "per_staff_credits", "is_available"]

    def clean(self):
        data = super().clean()
        for field in ("base_credits", "per_staff_credits"):
            value = data.get(field)
            if value is not None and not Decimal("0") <= value <= Decimal("1000000"):
                self.add_error(field, "Enter a nonnegative value up to 1,000,000 credits.")
        return data


class CreditAdjustmentForm(forms.Form):
    company = forms.ModelChoiceField(queryset=Company.objects.filter(is_active=True))
    delta = forms.DecimalField(max_digits=12, decimal_places=2)
    reason = forms.CharField(max_length=255, min_length=8)
    request_id = forms.UUIDField(widget=forms.HiddenInput)

    def clean_delta(self):
        value = self.cleaned_data["delta"]
        if value == Decimal("0") or abs(value) > Decimal("1000000"):
            raise forms.ValidationError("Change must be nonzero and within 1,000,000 credits.")
        return value
