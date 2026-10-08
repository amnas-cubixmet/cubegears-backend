from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0005_leaverequest_manager_note"),
    ]

    operations = [
        migrations.AlterField(
            model_name="leaverequest",
            name="manager_note",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="leavetype",
            name="allocation_method",
            field=models.CharField(
                choices=[
                    ("annual", "Annual"),
                    ("monthly", "Monthly"),
                    ("manual", "Manual"),
                ],
                default="annual",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="leavetype",
            name="monthly_allocation",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=6),
        ),
    ]
]
