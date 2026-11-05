import hashlib
import secrets
import uuid
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models, transaction
from django.utils import timezone
from .managers import UserManager

class User(AbstractBaseUser, PermissionsMixin):
    def save(self, *args, **kwargs):
        from apps.credits.change_tracking import observe_save
        with observe_save(self, kwargs, storage=False):
            return super().save(*args, **kwargs)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, null=True, blank=True)
    normalized_phone = models.CharField(max_length=17, null=True, blank=True, db_index=True)
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    company = models.ForeignKey("companies.Company", on_delete=models.SET_NULL, null=True, blank=True, related_name="users")
    branch = models.ForeignKey("branches.Branch", on_delete=models.SET_NULL, null=True, blank=True, related_name="users")
    role = models.ForeignKey("roles.Role", on_delete=models.SET_NULL, null=True, blank=True, related_name="users")
    avatar = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    email_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    objects = UserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "normalized_phone"], condition=models.Q(company__isnull=False, normalized_phone__isnull=False), name="unique_company_mobile_login"),
            models.UniqueConstraint(fields=["normalized_phone"], condition=models.Q(company__isnull=True, normalized_phone__isnull=False), name="unique_platform_mobile_login"),
        ]

    def __str__(self):
        return self.email or self.normalized_phone or str(self.pk)

class MagicLink(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="magic_links")
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def issue(cls, user):
        raw = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        minutes = getattr(settings, "MAGIC_LINK_EXPIRY_MINUTES", 15)
        cls.objects.create(
            user=user,
            token_hash=token_hash,
            expires_at=timezone.now() + timedelta(minutes=minutes),
        )
        return raw

    @classmethod
    @transaction.atomic
    def consume(cls, raw_token):
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        link = cls.objects.select_for_update().select_related("user").filter(
            token_hash=token_hash, used_at__isnull=True,
        ).first()
        if not link or link.expires_at <= timezone.now() or not link.user.is_active:
            return None
        link.used_at = timezone.now()
        link.save(update_fields=["used_at"])
        return link.user


class StaffPasswordInvitation(models.Model):
    """One-use HMAC capability. Raw URL secrets are never stored."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff_password_invitations")
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="issued_staff_password_invitations")
    nonce = models.CharField(max_length=64)
    token_hash = models.CharField(max_length=64, unique=True)
    purpose = models.CharField(max_length=10, default="setup", choices=[("setup","Setup"),("reset","Reset")])
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    delivery_status = models.CharField(max_length=24, default="pending")
    meta_message_id = models.CharField(max_length=180, blank=True, db_index=True)
    failure_reason = models.CharField(max_length=280, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
