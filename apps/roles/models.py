import uuid
from django.db import models

class Role(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="roles", null=True, blank=True)
    name = models.CharField(max_length=80)
    code = models.CharField(max_length=50)
    permissions = models.JSONField(default=list, blank=True)
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_role_code_per_company")
        ]

    def __str__(self):
        return self.name

    def allows(self, permission_code):
        return "*" in self.permissions or permission_code in self.permissions
