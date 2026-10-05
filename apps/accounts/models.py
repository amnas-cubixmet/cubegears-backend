import hashlib
import secrets
import uuid
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone
from .managers import UserManager

class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
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

    def __str__(self):
        return self.email

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
    def consume(cls, raw_token):
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        link = cls.objects.select_related("user").filter(token_hash=token_hash, used_at__isnull=True).first()
        if not link or link.expires_at <= timezone.now() or not link.user.is_active:
            return None
        link.used_at = timezone.now()
        link.save(update_fields=["used_at"])
        return link.user
