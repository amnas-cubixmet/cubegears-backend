from django.db import migrations, models


def clear_retired_labels(apps, schema_editor):
    registry = apps.get_model("companies", "CompanyWebsiteSubdomain")
    registry.objects.using(schema_editor.connection.alias).filter(company__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("companies", "0010_remove_custom_domains")]

    operations = [
        migrations.AlterField(
            model_name="companywebsitesubdomain",
            name="company",
            field=models.OneToOneField(
                null=True, on_delete=models.SET_NULL, related_name="website_subdomain",
                to="companies.company",
            ),
        ),
        migrations.RunPython(migrations.RunPython.noop, clear_retired_labels),
        migrations.AddField(
            model_name="companywebsitesubdomain",
            name="website_enabled",
            field=models.BooleanField(default=True),
        ),
    ]
