from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_mobile_login_staff_tokens"),
        ("branches", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="allowed_branches",
            field=models.ManyToManyField(
                blank=True, related_name="authorized_users", to="branches.branch",
            ),
        ),
    ]
