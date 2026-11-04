import uuid
import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


def backfill_canonical_phones(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    import re
    seen = set()
    # Historic local numbers without a country code remain unverified.
    # Conflicting old records are left without a canonical login number.
    for user in User.objects.exclude(phone="").order_by("created_at", "pk").iterator():
        raw = re.sub(r"[\s().-]", "", user.phone or "")
        if not re.fullmatch(r"\+[1-9][0-9]{7,14}", raw):
            continue
        identity = (str(user.company_id) if user.company_id else "platform", raw)
        if identity in seen:
            continue
        seen.add(identity)
        User.objects.filter(pk=user.pk).update(normalized_phone=raw)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]
    operations = [
        migrations.AlterField(
            model_name="user", name="email",
            field=models.EmailField(blank=True, null=True, max_length=254, unique=True),
        ),
        migrations.AddField(
            model_name="user", name="normalized_phone",
            field=models.CharField(blank=True, null=True, max_length=17, db_index=True),
        ),
        migrations.RunPython(backfill_canonical_phones, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="user", constraint=models.UniqueConstraint(
                fields=["company", "normalized_phone"],
                condition=Q(company__isnull=False, normalized_phone__isnull=False),
                name="unique_company_mobile_login",
            ),
        ),
        migrations.AddConstraint(
            model_name="user", constraint=models.UniqueConstraint(
                fields=["normalized_phone"],
                condition=Q(company__isnull=True, normalized_phone__isnull=False),
                name="unique_platform_mobile_login",
            ),
        ),
        migrations.CreateModel(
            name="StaffPasswordInvitation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("nonce", models.CharField(max_length=64)),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                ("purpose", models.CharField(max_length=10, default="setup", choices=[("setup","Setup"),("reset","Reset")])),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("invalidated_at", models.DateTimeField(blank=True, null=True)),
                ("delivery_status", models.CharField(max_length=24, default="pending")),
                ("meta_message_id", models.CharField(blank=True, max_length=180, db_index=True)),
                ("failure_reason", models.CharField(blank=True, max_length=280)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="staff_password_invitations", to="accounts.user")),
                ("issued_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="issued_staff_password_invitations", to="accounts.user")),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
