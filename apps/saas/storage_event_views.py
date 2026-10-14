from django.utils.dateparse import parse_date
from rest_framework import response
from rest_framework.views import APIView
from apps.accounts.permissions import RolePermission
from .models import StorageEvent
from .storage_ledger import as_dict

class StorageEventsView(APIView):
    permission_classes=[RolePermission]
    permission_code="storage.view"
    http_method_names=["get","head","options"]

    def get(self, request):
        p=request.query_params
        start,end=p.get("from",""),p.get("to","")
        if (start and not parse_date(start)) or (end and not parse_date(end)):
            return response.Response({"message":"Use YYYY-MM-DD."},status=400)
        if start and end and start>end:
            return response.Response({"message":"Invalid date range."},status=400)
        kind=(p.get("eventType") or "").upper()
        if kind and kind not in dict(StorageEvent.EVENT_TYPES):
            return response.Response({"eventType":["Invalid event type."]},status=400)
        try:
            limit,offset=int(p.get("limit",50)),int(p.get("offset",0))
        except (ValueError,TypeError):
            return response.Response({"message":"Invalid pagination."},status=400)
        if not 1<=limit<=100 or offset<0:
            return response.Response({"message":"Invalid pagination."},status=400)
        qs=StorageEvent.objects.filter(company=request.user.company).select_related("actor")
        if start: qs=qs.filter(occurred_at__date__gte=start)
        if end: qs=qs.filter(occurred_at__date__lte=end)
        if kind: qs=qs.filter(event_type=kind)
        return response.Response({"count":qs.count(),"results":[as_dict(e) for e in qs[offset:offset+limit]]})
