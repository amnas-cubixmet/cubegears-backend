from django.db import transaction
from django.db.models import Sum
from .storage_ledger import ensure_baseline, record_event
from django.utils import timezone
from rest_framework import parsers, response, status, serializers
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
    permission_code="billing.view"
    http_method_names=["get", "head", "options"]

class StorageUsageViewSet(CompanyScopedModelViewSet):
    queryset=StorageUsage.objects.all(); serializer_class=StorageUsageSerializer
    permission_code="storage.view"
    http_method_names=["get", "head", "options"]

class DocumentTemplateViewSet(CompanyScopedModelViewSet):
    queryset=DocumentTemplate.objects.all(); serializer_class=DocumentTemplateSerializer
    permission_map={"GET":"settings.view","POST":"settings.manage","PUT":"settings.manage","PATCH":"settings.manage","DELETE":"settings.manage"}

class SecurityEventViewSet(CompanyScopedModelViewSet):
    queryset=SecurityEvent.objects.select_related("user").all(); serializer_class=SecurityEventSerializer
    permission_code="settings.view"
    http_method_names=["get","head","options"]

class SubscriptionSummaryView(APIView):
    permission_classes = [RolePermission]
    permission_code = "billing.view"

    def get(self, request):
        """Authoritative plan and actual active account count, not editable usage."""
        company = request.user.company
        if not company:
            return response.Response({"message": "No company linked to this account."}, status=400)
        sub = Subscription.objects.filter(
            company=company, status__iexact="Active"
        ).order_by("-created_at").first()
        plan = sub.plan_definition if sub and sub.plan_definition_id and sub.plan_definition.is_active else None
        storage_bytes = MediaFile.objects.filter(company=company).aggregate(
            value=Sum("size_bytes")
        )["value"] or 0
        seats_used = company.users.filter(is_active=True).count()
        return response.Response({
            "plan": {
                "id": str(sub.plan if sub else company.plan or "").lower().replace(" ", "-"),
                "name": sub.plan if sub else "No active subscription",
                "status": sub.status.lower() if sub else "not_configured",
                "billingCycle": sub.billing_cycle if sub else None,
                "basePrice": float(sub.amount) if sub else None,
                "includedStorageGb": plan.included_storage_gb if plan else None,
                "maxBranches": plan.max_branches if plan else None,
                "trialDays": plan.trial_days if plan else None,
                "features": {a.feature.key: a.enabled for a in plan.feature_access.select_related("feature")} if plan else {},
                "extraStorageRate": float(plan.extra_storage_rate) if plan else None,
                "includedSeats": plan.max_users if plan else (sub.seats if sub else None),
                "extraSeatRate": None,
                "nextBillingDate": sub.renews_at.date().isoformat()
                if sub and sub.renews_at else None,
            },
            "usage": {
                "storageUsedGb": round(storage_bytes / (1024 ** 3), 3),
                "seatsUsed": seats_used,
            },
            "paymentMethod": None,
            "invoices": [],
            "billingDataAvailable": bool(sub),
            "billingNote": (
                "Subscription invoices and payment method are not connected to a "
                "billing provider yet. No historical charges are being reported."
            ),
        })


class StorageSummaryView(APIView):
    permission_classes=[RolePermission]
    permission_code="storage.view"
    def get(self,request):
        company=request.user.company
        files=MediaFile.objects.filter(company=company)
        used=files.aggregate(v=Sum("size_bytes"))["v"] or 0
        setting=CompanySetting.objects.filter(company=company,category="storage").first()
        storage_settings={**DEFAULTS["storage"],**(setting.data if setting else {})}
        active_sub = (Subscription.objects.filter(company=company, status__iexact="Active")
                      .select_related("plan_definition").order_by("-created_at").first())
        return response.Response({
            "quotaGb": active_sub.plan_definition.included_storage_gb if active_sub and active_sub.plan_definition_id else None,
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
        uploads = request.FILES.getlist("files")
        if not uploads:
            return response.Response({"message": "No files supplied."}, status=400)
        cfg = CompanySetting.objects.filter(company=request.user.company, category="storage").first()
        policy = {**DEFAULTS["storage"], **(cfg.data if cfg else {})}
        allowed = set(policy["allowedTypes"]) & set(DEFAULTS["storage"]["allowedTypes"])
        max_bytes = min(int(policy.get("maxFileMb") or 20), 20) * 1024 * 1024
        for upload in uploads:
            if upload.size <= 0 or upload.size > max_bytes or upload.content_type not in allowed:
                return response.Response({"message": "Invalid file type or size."}, status=400)
        created=[]
        try:
            with transaction.atomic():
                ensure_baseline(request.user.company)
                for upload in uploads:
                    obj=MediaFile.objects.create(
                        company=request.user.company,branch=request.user.branch,file=upload,name=upload.name,
                        category=category,mime_type=getattr(upload,"content_type","") or "",size_bytes=upload.size,
                        uploaded_by=request.user,
                    )
                    created.append(obj)
                    record_event(obj,"UPLOAD",request.user)
        except Exception:
            for obj in created:
                if obj.file: obj.file.delete(save=False)
            raise
        return response.Response(MediaFileSerializer(created,many=True,context={"request":request}).data,status=status.HTTP_201_CREATED)

class StorageFilesDeleteView(APIView):
    permission_classes=[RolePermission]
    permission_code="storage.manage"
    def post(self,request):
        ids=request.data.get("ids") or []
        qs=MediaFile.objects.filter(company=request.user.company,id__in=ids)
        deleted=0
        with transaction.atomic():
            ensure_baseline(request.user.company)
            for obj in qs:
                name=obj.file.name if obj.file else None
                storage=obj.file.storage if name else None
                obj.delete()
                record_event(obj,"DELETE",request.user)
                if name:
                    transaction.on_commit(lambda storage=storage,name=name: storage.delete(name))
                deleted+=1
        return response.Response({"deleted":deleted})

class StorageSettingsView(APIView):
    permission_classes=[RolePermission]
    permission_code="storage.manage"
    def put(self,request):
        obj,_=CompanySetting.objects.get_or_create(company=request.user.company,category="storage",defaults={"branch":request.user.branch,"data":{}})
        obj.data={**obj.data,**request.data}; obj.save(update_fields=["data","updated_at"])
        return StorageSummaryView().get(request)

class StorageHistoryListView(APIView):
    permission_classes = [RolePermission]
    permission_code = "storage.view"

    def get(self, request):
        """Return stored company-scoped snapshots only, never invented daily usage."""
        company = request.user.company
        if not company:
            return response.Response({"message": "No company linked to this account."}, status=400)

        from_date = request.query_params.get("from", "")
        to_date = request.query_params.get("to", "")
        from django.utils.dateparse import parse_date
        if from_date and not parse_date(from_date):
            return response.Response({"from": ["Use YYYY-MM-DD."]}, status=400)
        if to_date and not parse_date(to_date):
            return response.Response({"to": ["Use YYYY-MM-DD."]}, status=400)
        if from_date and to_date and from_date > to_date:
            return response.Response({"message": "From date must be before To date."}, status=400)

        entries = StorageUsage.objects.filter(company=company)
        if from_date:
            entries = entries.filter(date__gte=from_date)
        if to_date:
            entries = entries.filter(date__lte=to_date)
        grouped = {}
        for entry in entries:
            key = entry.date.isoformat()
            day = grouped.setdefault(key, {"date": key, "bytes": 0, "fileCount": 0})
            day["bytes"] += max(0, entry.bytes_used)
            day["fileCount"] += entry.file_count
        return response.Response([
            {
                "date": item["date"],
                "usedGb": round(item["bytes"] / (1024 ** 3), 3),
                "fileCount": item["fileCount"],
                "rate": None,
                "charge": None,
                "snapshotType": "stored",
            }
            for _, item in sorted(grouped.items(), reverse=True)
        ])


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

class CompanyAccountSettingsSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=180, required=False, allow_blank=False)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    gstNo = serializers.RegexField(
        regex=r"^([0-9]{2}[A-Z0-9]{13})?$", required=False,
        allow_blank=True, max_length=20,
    )
    address = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    currency = serializers.ChoiceField(
        choices=["INR", "USD", "AED", "SAR"], required=False,
    )


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
        if not category:
            return response.Response({"message": "Category is required."}, status=400)
        if not request.user.company:
            return response.Response({"message": "No company linked to this account."}, status=400)
        payload = request.data
        if not isinstance(payload, dict):
            return response.Response({"message": "A settings object is required."}, status=400)
        if category == "company":
            # Reject unknown keys rather than silently saving shadow company data.
            allowed = set(CompanyAccountSettingsSerializer().fields) | {"timezone", "dateFormat"}
            unknown = set(payload) - allowed
            if unknown:
                return response.Response(
                    {"message": "Unsupported company fields: " + ", ".join(sorted(unknown))},
                    status=400,
                )
            serializer = CompanyAccountSettingsSerializer(data=payload, partial=True)
            serializer.is_valid(raise_exception=True)
            payload = dict(serializer.validated_data)
            if "timezone" in request.data:
                timezone = request.data["timezone"]
                if timezone not in ("Asia/Kolkata", "Asia/Dubai", "Asia/Riyadh", "UTC"):
                    return response.Response({"timezone": ["Choose a supported timezone."]}, status=400)
                payload["timezone"] = timezone
            if "dateFormat" in request.data:
                date_format = request.data["dateFormat"]
                if date_format not in ("DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD"):
                    return response.Response({"dateFormat": ["Choose a supported date format."]}, status=400)
                payload["dateFormat"] = date_format
            company = request.user.company
            mapping = {
                "name": "name", "phone": "phone", "email": "email",
                "gstNo": "gstin", "address": "address", "currency": "currency",
            }
            updated = []
            for key, target in mapping.items():
                if key in serializer.validated_data:
                    setattr(company, target, serializer.validated_data[key])
                    updated.append(target)
            if updated:
                company.save(update_fields=updated + ["updated_at"])
        setting, _ = CompanySetting.objects.get_or_create(
            company=request.user.company, category=category,
            defaults={"branch": request.user.branch, "data": {}},
        )
        # Account profile editor sends only five company fields; preserve other
        # previously saved company locale and document preferences.
        setting.data = {**setting.data, **payload}
        setting.save(update_fields=["data", "updated_at"])
        return self.get(request, category)
