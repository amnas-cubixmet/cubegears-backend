from django.db import models
from common.models import CompanyOwnedModel
class Notification(CompanyOwnedModel):
    user=models.ForeignKey("accounts.User",on_delete=models.CASCADE,null=True,blank=True,related_name="notifications")
    title=models.CharField(max_length=180)
    message=models.TextField()
    notification_type=models.CharField(max_length=50,default="info")
    data=models.JSONField(default=dict,blank=True)
    is_read=models.BooleanField(default=False)
    read_at=models.DateTimeField(null=True,blank=True)
    class Meta: ordering=["-created_at"]
