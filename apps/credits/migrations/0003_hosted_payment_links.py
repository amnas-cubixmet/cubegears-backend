from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("credits", "0002_feature_bundles")]

    operations = [
        migrations.AddField(
            model_name="creditpurchase",
            name="payment_link_id",
            field=models.CharField(blank=True, max_length=80, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="creditpurchase",
            name="checkout_url",
            field=models.URLField(blank=True, max_length=500),
        ),
    ]
