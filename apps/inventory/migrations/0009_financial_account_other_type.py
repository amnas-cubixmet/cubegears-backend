from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0008_workshopfinancialaccount_account_number_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workshopfinancialaccount",
            name="kind",
            field=models.CharField(
                max_length=16,
                choices=[
                    ("Cash", "Cash"),
                    ("Bank", "Bank"),
                    ("UPI", "UPI"),
                    ("Wallet", "Wallet"),
                    ("Other", "Other"),
                ],
            ),
        ),
    ]
