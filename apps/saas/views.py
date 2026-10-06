from django.db.models import Sum
from django.utils import timezone
from rest_framework import parsers, response, status
from apps.accounts.permissions import RolePermission
from rest_framework.views import APIView
from common.viewsets import CompanyScopedModelViewSet
from .models import Subscription,StorageUsage,DocumentTemplate,CompanySetting,SecurityEvent,MediaFile
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
"storage":{"autoCompress":True,"keepOriginals":False,"allowStaffUpload":True,"allowStaffDelete":False,"retentionDays":0,"maxFileMb":20,"allowedTypes":["image/jpeg","image/png","image/webp"]},
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

class SubscriptionSummaryView(APIView):
    permission_classes=[RolePermission]
    permission_map={"GET":"billing.view","PUT":"billing.edit"}
    def get(self,request):
        company=request.user.company
        sub=Subscription.objects.filter(company=company,status__iexact="Active").order_by("-created_at").first()
        storage_bytes=MediaFile.objects.filter(company=company).aggregate(v=Sum("size_bytes"))["v"] or 0
        seats_used=company.users.filter(is_active=True).count()
        usage_setting=CompanySetting.objects.filter(company=company,category="saas_usage").first()
        if usage_setting and usage_setting.data.get("seatsUsed") is not None:
            seats_used=usage_setting.data["seatsUsed"]
        plan_name=sub.plan if sub else company.plan
        included_seats=sub.seats if sub else 5
        return response.Response({
            "plan":{
                "id":str(plan_name).lower().replace(" ","-"),
                "name":plan_name,
                "status":(sub.status if sub else "active").lower(),
                "billingCycle":sub.billing_cycle if sub else "monthly",
                "basePrice":float(sub.amount) if sub else 0,
                "includedStorageGb":25,
                "extraStorageRate":15,
                "includedSeats":included_seats,
                "extraSeatRate":199,
                "nextBillingDate":sub.renews_at.date().isoformat() if sub and sub.renews_at else None,
            },
            "usage":{"storageUsedGb":round(storage_bytes/(1024**3),3),"seatsUsed":seats_used},
            "paymentMethod":{"type":"UPI / Card","label":"Primary billing method","status":"ready"},
            "invoices":[],
        })
    def put(self,request):
        obj,_=CompanySetting.objects.get_or_create(company=request.user.company,category="saas_usage",defaults={"branch":request.user.branch,"data":{}})
        obj.data={**obj.data,"seatsUsed":max(1,int(request.data.get("seatsUsed",1)))}
        obj.save(update_fields=["data","updated_at"])
        return self.get(request)

class StorageSummaryView(APIView):
    permission_classes=[RolePermission]
    permission_code="storage.view"
    def get(self,request):
        company=request.user.company
        files=MediaFile.objects.filter(company=company)
        used=files.aggregate(v=Sum("size_bytes"))["v"] or 0
        setting=CompanySetting.objects.filter(company=company,category="storage").first()
        storage_settings={**DEFAULTS["storage"],**(setting.data if setting else {})}
        return response.Response({
            "quotaGb":25,
            "usedGb":round(used/(1024**3),3),
            "settings":storage_settings,
            "files":MediaFileSerializer(files[:500],many=True,context={"request":request}).data,
        })

class StorageFilesView(APIView):
    permission_classes=[RolePermission]
    permission_code="storage.manage"
    parser_classes=[parsers.MultiPartParser,parsers.FormParser]
    def post(self,request):
        category=request.data.get("category","General")
        created=[]
        for upload in request.FILES.getlist("files"):
            obj=MediaFile.objects.create(
                company=request.user.company,branch=request.user.branch,file=upload,name=upload.name,
                category=category,mime_type=getattr(upload,"content_type","") or "",size_bytes=upload.size,
                uploaded_by=request.user,
            )
            created.append(obj)
        return response.Response(MediaFileSerializer(created,many=True,context={"request":request}).data,status=status.HTTP_201_CREATED)

class StorageFilesDeleteView(APIView):
    permission_classes=[RolePermission]
    permission_code="storage.manage"
    def post(self,request):
        ids=request.data.get("ids") or []
        qs=MediaFile.objects.filter(company=request.user.company,id__in=ids)
        deleted=0
        for obj in qs:
            if obj.file:
                obj.file.delete(save=False)
            obj.delete(); deleted+=1
        return response.Response({"deleted":deleted})

class StorageSettingsView(APIView):
    permission_classes=[RolePermission]
    permission_code="storage.manage"
    def put(self,request):
        obj,_=CompanySetting.objects.get_or_create(company=request.user.company,category="storage",defaults={"branch":request.user.branch,"data":{}})
        obj.data={**obj.data,**request.data}; obj.save(update_fields=["data","updated_at"])
        return StorageSummaryView().get(request)

class StorageHistoryDateView(APIView):
    permission_classes=[RolePermission]
    permission_code="storage.view"
    def get(self,request,date):
        usage=StorageUsage.objects.filter(company=request.user.company,date=date)
        files=MediaFile.objects.filter(company=request.user.company,created_at__date=date)
        return response.Response({
            "date":date,
            "usage":StorageUsageSerializer(usage,many=True).data,
            "files":MediaFileSerializer(files,many=True,context={"request":request}).data,
        })

class SettingsView(APIView):
    permission_classes=[RolePermission]
    permission_map={"GET":"settings.view","PUT":"settings.manage"}
    def get(self,request,category=None):
        qs=CompanySetting.objects.filter(company=request.user.company)
        if category:
            obj=qs.filter(category=category).first()
            result={**DEFAULTS.get(category,{}),**(obj.data if obj else {})}
            if category=="company":
                c=request.user.company
                result={**result,"name":c.name,"phone":c.phone,"email":c.email,"gstNo":c.gstin,"address":c.address,"currency":c.currency,"logo":c.logo}
            return response.Response(result)
        result={k:(v.copy() if isinstance(v,dict) else v) for k,v in DEFAULTS.items()}
        for obj in qs: result[obj.category]={**result.get(obj.category,{}),**obj.data}
        c=request.user.company
        result["company"]={**result["company"],"name":c.name,"phone":c.phone,"email":c.email,"gstNo":c.gstin,"address":c.address,"currency":c.currency,"logo":c.logo}
        return response.Response(result)
    def put(self,request,category=None):
        if not category: return response.Response({"message":"Category is required."},status=400)
        if category=="company":
            c=request.user.company
            mapping={"name":"name","phone":"phone","email":"email","gstNo":"gstin","address":"address","currency":"currency","logo":"logo"}
            for incoming,attr in mapping.items():
                if incoming in request.data: setattr(c,attr,request.data[incoming])
            c.save()
        obj,_=CompanySetting.objects.update_or_create(company=request.user.company,category=category,defaults={"branch":request.user.branch,"data":request.data})
        return response.Response(obj.data)
