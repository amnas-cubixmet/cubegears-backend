from django.contrib.auth.base_user import BaseUserManager

class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email=None, password=None, **extra_fields):
        from .phone import normalize_phone
        email = self.normalize_email(email) if email else None
        mobile = normalize_phone(extra_fields.get("phone"), optional=True)
        if not email and not mobile:
            raise ValueError("Provide an email or international mobile number.")
        if mobile:
            extra_fields["phone"] = mobile
            extra_fields["normalized_phone"] = mobile
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        return self.create_user(email, password, **extra_fields)
