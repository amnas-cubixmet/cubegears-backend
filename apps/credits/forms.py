from decimal import Decimal
from django import forms
from apps.companies.models import Company
from apps.saas.models import PlanFeature
from .models import CreditPackage, FeatureCreditPrice, StorageCreditPolicy


class CreditPackageForm(forms.ModelForm):
    # "Unlimited" means no administrator-chosen sequence number is required:
    # new packages are appended using the next valid database sort position.
    sort_order_mode = forms.ChoiceField(
        choices=[("unlimited", "Unlimited (automatic)"), ("manual", "Custom order")],
        initial="unlimited",
        required=False,
    )

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
        mode = self.data.get("sort_order_mode", "").strip()
        value = self.cleaned_data.get("sort_order")
        if mode == "manual" and value is None:
            raise forms.ValidationError("Enter a non-negative display order number.")
        if mode != "unlimited" and value is not None:
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
