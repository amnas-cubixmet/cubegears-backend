import uuid
from django.db import models
from common.models import CompanyOwnedModel


class WhatsAppBusinessConnection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.OneToOneField("companies.Company", on_delete=models.CASCADE, related_name="whatsapp_connection")
    waba_id = models.CharField(max_length=100, blank=True)
    phone_number_id = models.CharField(max_length=100, blank=True)
    display_phone_number = models.CharField(max_length=40, blank=True)
    encrypted_access_token = models.TextField(blank=True)
    connected = models.BooleanField(default=False)
    notifications_enabled = models.BooleanField(default=False)
    event_settings = models.JSONField(default=dict, blank=True)
    connected_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class CustomerNotificationPreference(CompanyOwnedModel):
    customer = models.OneToOneField("customers.Customer", on_delete=models.CASCADE, related_name="notification_preference")
    whatsapp_opt_in = models.BooleanField(default=False)
    consent_source = models.CharField(max_length=120, blank=True)
    opted_in_at = models.DateTimeField(null=True, blank=True)
    opted_out_at = models.DateTimeField(null=True, blank=True)


class WhatsAppMessageTemplate(CompanyOwnedModel):
    event = models.CharField(max_length=60)
    template_name = models.CharField(max_length=180)
    language = models.CharField(max_length=24, default="en_US")
    approval_status = models.CharField(max_length=24, default="UNKNOWN")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "event"], name="wa_template_company_event_uniq")]


class WhatsAppNotificationLog(CompanyOwnedModel):
    customer = models.ForeignKey("customers.Customer", on_delete=models.SET_NULL, null=True, blank=True)
    job = models.ForeignKey("jobs.Job", on_delete=models.SET_NULL, null=True, blank=True)
    event = models.CharField(max_length=60)
    idempotency_key = models.CharField(max_length=180)
    phone = models.CharField(max_length=40)
    template_name = models.CharField(max_length=180, blank=True)
    status = models.CharField(max_length=24, default="pending")
    meta_message_id = models.CharField(max_length=180, blank=True, db_index=True)
    error_detail = models.CharField(max_length=500, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["company", "idempotency_key"], name="wa_company_idempotent_uniq")]
