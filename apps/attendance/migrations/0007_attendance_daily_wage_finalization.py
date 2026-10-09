import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("attendance", "0006_leave_allocation_periods"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(
            model_name="attendancerecord", name="wage_finalized",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="attendancerecord", name="wage_finalized_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="attendancerecord", name="wage_finalized_by",
            field=models.ForeignKey(
                to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.SET_NULL,
                null=True, blank=True, related_name="approved_daily_wage_attendance",
            ),
        ),
    ]
