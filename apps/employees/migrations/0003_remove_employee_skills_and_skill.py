from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0002_employee_activity"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="employee",
            name="skills",
        ),
        migrations.DeleteModel(
            name="Skill",
        ),
    ]
