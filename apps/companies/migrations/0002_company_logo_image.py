# Add company logo image storage without altering existing URL-based logos.
from django.db import migrations, models
import apps.companies.models


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="logo_image",
            field=models.ImageField(blank=True, upload_to=apps.companies.models.company_logo_upload_to),
        ),
    ]
