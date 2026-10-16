from decimal import Decimal
from django import forms
from apps.companies.models import Company
from apps.saas.models import PlanFeature
from .models import CreditPackage, FeatureCreditPrice, StorageCreditPolicy


class CreditPackageForm(forms.ModelForm):
    class Meta:
        model = CreditPackage
        fields = ["name", "credits", "price_inr", "is_active", "sort_order"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # ModelForm otherwise marks PositiveIntegerField(required=True) even
        # though its model default is 0. A blank order must be accepted.
        self.fields["sort_order"].required = False
        self.fields["sort_order"].help_text = (
            "Optional. Leave blank to place new packages after existing packages."
        )

    def clean_sort_order(self):
        value = self.cleaned_data.get("sort_order")
        if value is not None:
            return value
        if self.instance.pk is not None:
            # A blank field during an edit must not reorder the package.
            return self.instance.sort_order

        from django.db.models import Max
        latest = CreditPackage.objects.aggregate(last=Max("sort_order"))["last"]
        if latest is None:
            return 0
        # PositiveIntegerField is backed by a finite database integer type.
        # Avoid overflow if the last manually entered order is already huge.
        # A last value at SQLite/PostgreSQL's positive integer maximum cannot
        # be incremented; placing at zero still produces a deterministic order.
        return latest + 1 if latest < 2147483647 else 0

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
        fields = ["feature", "base_credits", "activation_credits", "per_user_credits",
                  "per_staff_credits", "billing_period", "is_available"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.saas.feature_discovery import module_inventory
        allowed = [module["key"] for module in module_inventory() if module["billable"]]
        self.fields["feature"].queryset = PlanFeature.objects.filter(key__in=allowed, is_active=True)
        # Older pricing forms remain compatible; new fields default to zero.
        for name in ("activation_credits", "per_user_credits"):
            self.fields[name].required = False

    def clean(self):
        data = super().clean()
        for name in ("activation_credits", "per_user_credits"):
            if data.get(name) is None:
                data[name] = Decimal("0.00")
        for name in ("base_credits", "activation_credits", "per_user_credits", "per_staff_credits"):
            value = data.get(name)
            if value is not None and not Decimal("0") <= value <= Decimal("1000000"):
                self.add_error(name, "Enter a nonnegative value up to 1,000,000 credits.")
        return data


class StorageCreditPolicyForm(forms.ModelForm):
    class Meta:
        model = StorageCreditPolicy
        fields = ["included_gb", "credits_per_gb", "billing_period", "is_active"]

    def clean(self):
        data = super().clean()
        included = data.get("included_gb")
        rate = data.get("credits_per_gb")
        if included is not None and not Decimal("0") <= included <= Decimal("1000000"):
            self.add_error("included_gb", "Enter a nonnegative allowance.")
        if rate is not None and not Decimal("0") <= rate <= Decimal("1000000"):
            self.add_error("credits_per_gb", "Enter a nonnegative credit price.")
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
