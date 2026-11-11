"""Remove obsolete per-workshop custom-domain registrations.

Historical migrations are retained for existing databases. This forward
migration removes the now-unused custom-domain records and audit tables;
default CompanyWebsiteSubdomain reservations and portal settings remain.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("companies", "0009_company_website_subdomain")]

    operations = [
        migrations.DeleteModel(name="CompanyDomainAudit"),
        migrations.DeleteModel(name="CompanyDomain"),
    ]
