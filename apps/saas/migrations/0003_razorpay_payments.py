import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies=[("saas","0002_dynamic_plans"),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.CreateModel(name="PaymentPolicy",fields=[
            ("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),
            ("monthly_access_days",models.PositiveIntegerField(default=30)),
            ("yearly_access_days",models.PositiveIntegerField(default=365)),
            ("enforce_paywall",models.BooleanField(default=False)),
            ("updated_at",models.DateTimeField(auto_now=True)),
        ]),
        migrations.CreateModel(name="SubscriptionPayment",fields=[
            ("id",models.UUIDField(default=uuid.uuid4,editable=False,primary_key=True,serialize=False)),
            ("created_at",models.DateTimeField(auto_now_add=True)),
            ("updated_at",models.DateTimeField(auto_now=True)),
            ("order_id",models.CharField(max_length=100,unique=True)),
            ("payment_id",models.CharField(max_length=100,unique=True,null=True,blank=True)),
            ("billing_cycle",models.CharField(max_length=10,choices=[("monthly","Monthly"),("yearly","Yearly")])),
            ("amount_paise",models.PositiveBigIntegerField()),
            ("currency",models.CharField(max_length=3,default="INR")),
            ("status",models.CharField(max_length=16,default="created",db_index=True)),
            ("gateway_method",models.CharField(max_length=50,blank=True)),
            ("paid_at",models.DateTimeField(null=True,blank=True)),
            ("access_until",models.DateTimeField(null=True,blank=True)),
            ("gateway_data",models.JSONField(default=dict,blank=True)),
            ("branch",models.ForeignKey(to="branches.branch",null=True,blank=True,on_delete=django.db.models.deletion.SET_NULL)),
            ("company",models.ForeignKey(to="companies.company",on_delete=django.db.models.deletion.CASCADE)),
            ("plan",models.ForeignKey(to="saas.subscriptionplan",on_delete=django.db.models.deletion.PROTECT)),
            ("requested_by",models.ForeignKey(to=settings.AUTH_USER_MODEL,null=True,on_delete=django.db.models.deletion.SET_NULL)),
        ],options={"ordering":["-created_at"],"indexes":[models.Index(fields=["company","created_at"],name="saas_pay_company_at")]}),
    ]
