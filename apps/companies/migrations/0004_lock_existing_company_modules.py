"""Apply paid-module checks to existing companies without changing purchases."""
from django.db import migrations


def lock_existing_companies(apps, schema_editor):
    Company = apps.get_model('companies', 'Company')
    Company.objects.using(schema_editor.connection.alias).filter(
        require_paid_features=False,
    ).update(require_paid_features=True)


class Migration(migrations.Migration):
    dependencies = [('companies', '0003_company_account_owner')]
    operations = [migrations.RunPython(lock_existing_companies, migrations.RunPython.noop)]
