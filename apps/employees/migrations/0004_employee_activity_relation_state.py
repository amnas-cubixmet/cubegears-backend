import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("employees", "0003_remove_employee_skills_and_skill")]

    operations = [
        migrations.AlterField(
            model_name="employeeactivity",
            name="branch",
            field=models.ForeignKey(
                to="branches.branch", on_delete=django.db.models.deletion.SET_NULL,
                null=True, blank=True,
            ),
        ),
        migrations.AlterField(
            model_name="employeeactivity",
            name="company",
            field=models.ForeignKey(
                to="companies.company", on_delete=django.db.models.deletion.CASCADE,
            ),
        ),
    ]
