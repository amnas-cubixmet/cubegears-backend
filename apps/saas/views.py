from django.db.models import Sum
from rest_framework import generics,response
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from common.viewsets import CompanyScopedModelViewSet
from .models import Subscription,StorageUsage,DocumentTemplate,CompanySetting,SecurityEvent
from .serializers import *

DEFAULTS={
"company":{"timezone":"Asia/Kolkata","dateFormat":"DD/MM/YYYY"},
"operations":{"workingStart":"09:00","workingEnd":"18:30","bookingSlotMinutes":30,"bookingCapacity":4,"jobPrefix":"JOB-","deliveryCreditAllowed":False},
"billing":{"invoicePrefix":"INV-","defaultTaxRate":18,"paymentTermsDays":0,"rounding":True},
"inventory":{"defaultMinimumStock":5,"defaultReorderQty":10,"lowStockAlerts":True,"negativeStock":False},
"attendance":{"graceMinutes":15,"overtimeAfterMinutes":540,"locationRequired":False,"correctionApproval":True},
"payroll":{"payrollDay":30,"overtimeMultiplier":1.5,"managerApproval":True},
"notifications":{"lowStock":True,"overdueInvoice":True,"jobReady":True,"leaveDecision":True,"email":False,"whatsapp":False,"browserPush":True},
"security":{"sessionHours":12,"requireStrongPassword":True,"auditExports":True,"twoFactor":False},
"integrations":{"razorpayEnabled":False,"whatsappEnabled":False,"emailEnabled":False,"webhookUrl":""},
}

class SubscriptionViewSet(CompanyScopedModelViewSet):
    queryset=Subscription.objects.all(); serializer_class=SubscriptionSerializer
class StorageUsageViewSet(CompanyScopedModelViewSet):
    queryset=StorageUsage.objects.all(); serializer_class=StorageUsageSerializer
class DocumentTemplateViewSet(CompanyScopedModelViewSet):
    queryset=DocumentTemplate.objects.all(); serializer_class=DocumentTemplateSerializer
class SecurityEventViewSet(CompanyScopedModelViewSet):
    queryset=SecurityEvent.objects.select_related("user").all(); serializer_class=SecurityEventSerializer
    http_method_names=["get","head","options"]

class StorageSummaryView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request):
        qs=StorageUsage.objects.filter(company=request.user.company)
        total=qs.aggregate(bytes=Sum("bytes_used"),files=Sum("file_count"))
        return response.Response({"bytesUsed":total["bytes"] or 0,"fileCount":total["files"] or 0,"history":StorageUsageSerializer(qs[:90],many=True).data})

class SettingsView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request,category=None):
        qs=CompanySetting.objects.filter(company=request.user.company)
        if category:
            obj=qs.filter(category=category).first()
            return response.Response({**DEFAULTS.get(category,{}),**(obj.data if obj else {})})
        result={k:v.copy() for k,v in DEFAULTS.items()}
        for obj in qs: result[obj.category]={**result.get(obj.category,{}),**obj.data}
        c=request.user.company
        result["company"]={**result["company"],"name":c.name,"phone":c.phone,"email":c.email,"gstNo":c.gstin,"address":c.address,"currency":c.currency}
        return response.Response(result)
    def put(self,request,category=None):
        if not category: return response.Response({"message":"Category is required."},status=400)
        obj,_=CompanySetting.objects.update_or_create(company=request.user.company,category=category,defaults={"branch":request.user.branch,"data":request.data})
        return response.Response(obj.data)
