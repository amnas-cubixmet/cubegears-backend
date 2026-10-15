from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def identify_existing_owners(apps, schema_editor):
    Company = apps.get_model('companies', 'Company')
    User = apps.get_model('accounts', 'User')
    alias = schema_editor.connection.alias
    # Match the recorded company email and system owner role, never arbitrary staff.
    for company in Company.objects.using(alias).exclude(email='').iterator():
        users = User.objects.using(alias).filter(
            company_id=company.pk, email__iexact=company.email,
            role__company_id=company.pk, role__code='SUPER_ADMIN',
            role__is_system=True,
        )
        if users.count() == 1:
            Company.objects.using(alias).filter(pk=company.pk).update(owner_id=users.first().pk)


class Migration(migrations.Migration):
    dependencies = [
        ('companies', '0002_company_logo_image'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(
            model_name='company', name='owner',
            field=models.ForeignKey(
                to=settings.AUTH_USER_MODEL, null=True, blank=True,
                related_name='owned_companies', on_delete=django.db.models.deletion.SET_NULL,
            ),
        ),
        migrations.AddField(
            model_name='company', name='require_paid_features',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(identify_existing_owners, migrations.RunPython.noop),
    ]
