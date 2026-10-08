from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="job",
            name="customer_feedback",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="job",
            name="customer_rating",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
    ]
