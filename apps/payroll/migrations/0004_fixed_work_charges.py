from decimal import Decimal

from django.db import migrations, models


PAYMENT_CHOICES = [
    ("monthly", "Fixed Monthly Salary"),
    ("daily", "Daily Wage"),
    ("hourly", "Hourly Wage"),
    ("per_job", "Fixed Charge per Job Work"),
    ("commission", "Commission Only"),
    ("monthly_commission", "Monthly Salary + Commission"),
    ("daily_commission", "Daily Wage + Commission"),
    ("hourly_commission", "Hourly Wage + Commission"),
    ("salary_incentive", "Fixed Salary + Job Incentive"),
    ("hybrid", "Custom Hybrid Compensation"),
]


class Migration(migrations.Migration):
    dependencies = [
        ("payroll", "0003_job_work_sessions"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobworksession",
            name="worker_charge",
            field=models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0")),
        ),
        migrations.AlterField(
            model_name="payrollpolicy",
            name="default_payment_type",
            field=models.CharField(max_length=40, choices=PAYMENT_CHOICES, default="monthly"),
        ),
        migrations.AlterField(
            model_name="employeecompensationplan",
            name="payment_type",
            field=models.CharField(max_length=40, choices=PAYMENT_CHOICES, default="monthly"),
        ),
    ]
