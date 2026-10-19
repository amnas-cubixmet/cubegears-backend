from django.db import models
from common.models import CompanyOwnedModel

class PaymentPolicy(models.Model):
    """Configured in Django admin; renewal terms are not client editable."""
    monthly_access_days = models.PositiveIntegerField(default=30)
    yearly_access_days = models.PositiveIntegerField(default=365)
    enforce_paywall = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def current(cls):
        return cls.objects.get_or_create(pk=1)[0]

class SubscriptionPayment(CompanyOwnedModel):
    order_id = models.CharField(max_length=100, unique=True)
    payment_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    plan = models.ForeignKey("saas.SubscriptionPlan", on_delete=models.PROTECT)
    requested_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL)
    billing_cycle = models.CharField(max_length=10, choices=[("monthly","Monthly"),("yearly","Yearly")])
    amount_paise = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(max_length=16, default="created", db_index=True)
    gateway_method = models.CharField(max_length=50, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    access_until = models.DateTimeField(null=True, blank=True)
    gateway_data = models.JSONField(default=dict, blank=True)
    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company","created_at"],name="saas_pay_company_at")]
