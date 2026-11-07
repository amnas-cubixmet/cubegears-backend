"""Tenant-scoped GST review APIs. These never submit returns to the GST Portal."""
from datetime import date

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import RolePermission
from apps.companies.models import Company
from .itc import approve_credit, claim_info
from .models import (
    GSTR2BImport, GSTFilingReview, GSTInputTaxClaim,
    GSTInputTaxReversal, GSTSalesCreditNote,
)
from .sales_credit import note_info, post_credit_note
from .services import parse_period


def period(request):
    return parse_period(request.query_params.get("period"))["period"]


class GSTR2BHistory(APIView):
    permission_classes = [RolePermission]
    permission_code = "gstr.view"

    def get(self, request):
        selected = period(request)
        batches = GSTR2BImport.objects.filter(
            company=request.user.company, period=selected,
        ).order_by("-created_at")[:20]
        return Response([{
            "id": str(row.pk), "period": row.period,
            "fingerprint": row.fingerprint,
            "matchedCount": sum(1 for result in row.rows if result["status"] == "Matched"),
            "unsupportedSections": row.unsupported_sections,
            "importedAt": row.created_at.isoformat(),
        } for row in batches])


class GSTITCApprovals(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["gstr.view"], "POST": ["gstr.reconcile"]}

    def get(self, request):
        selected = period(request)
        qs = GSTInputTaxClaim.objects.filter(
            company=request.user.company, period=selected,
        ).select_related("bill").order_by("-created_at")
        reversals = GSTInputTaxReversal.objects.filter(
            company=request.user.company, period=selected,
        ).select_related("claim__bill", "debit_note")
        return Response({
            "claims": [claim_info(row) for row in qs],
            "reversals": [{
                "id": str(row.pk), "debitNoteId": str(row.debit_note_id),
                "debitNoteNumber": row.debit_note.number,
                "invoiceNumber": row.claim.bill.invoice_number,
                "cgst": str(row.cgst), "sgst": str(row.sgst), "igst": str(row.igst),
                "period": row.period,
            } for row in reversals],
        })

    def post(self, request):
        claim = approve_credit(request)
        return Response(claim_info(claim), status=201)


class GSTOutwardCreditNotes(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["gstr.view"], "POST": ["gstr.reconcile"]}

    def get(self, request):
        selected = period(request)
        notes = GSTSalesCreditNote.objects.filter(
            company=request.user.company, date__year=int(selected[:4]),
            date__month=int(selected[5:7]),
        ).select_related("invoice").order_by("-date", "-created_at")[:300]
        return Response([note_info(row) for row in notes])

    def post(self, request):
        note, created = post_credit_note(request)
        return Response(note_info(note), status=201 if created else 200)


class GSTFilingReviews(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["gstr.view"], "POST": ["gstr.reconcile"]}

    def get(self, request):
        selected = period(request)
        review = GSTFilingReview.objects.filter(
            company=request.user.company, period=selected,
        ).first()
        if not review:
            return Response({
                "period": selected, "status": "Not Reviewed",
                "portalVerified": False,
            })
        return Response({
            "period": review.period, "status": review.status,
            "reference": review.reference,
            "filedOn": review.filed_on.isoformat() if review.filed_on else None,
            "note": review.note, "portalVerified": False,
            "reviewedAt": review.created_at.isoformat(),
        })

    @transaction.atomic
    def post(self, request):
        selected = period(request)
        Company.objects.select_for_update().get(pk=request.user.company_id)
        desired = str(request.data.get("status") or "")
        if desired not in {"Reviewed", "Reported Filed"}:
            raise ValidationError({"status": "Choose Reviewed or Reported Filed."})
        memo = str(request.data.get("note") or "").strip()
        if len(memo) < 15 or len(memo) > 1000:
            raise ValidationError({"note": "Provide an independent 15–1000 character review note."})
        evidence = str(request.data.get("reference") or "").strip()
        raw_date = request.data.get("filedOn")
        file_date = None
        if raw_date:
            try:
                file_date = date.fromisoformat(str(raw_date))
            except (ValueError, TypeError):
                raise ValidationError({"filedOn": "Use a valid YYYY-MM-DD date."})
        if desired == "Reported Filed" and (not evidence or not file_date):
            raise ValidationError({
                "reference": "Enter the portal acknowledgment and filing date; "
                             "the system cannot independently verify GST Portal submissions.",
            })
        record = GSTFilingReview.objects.select_for_update().filter(
            company=request.user.company, period=selected,
        ).first()
        if record and record.status == "Reported Filed":
            if (record.status != desired or record.reference != evidence
                    or record.filed_on != file_date or record.note != memo):
                raise ValidationError({
                    "status": "Reported filing evidence is immutable; contact the tax administrator for correction.",
                })
            return Response({"period": selected, "status": record.status,
                             "portalVerified": False}, status=200)
        if record:
            record.status = desired
            record.note = memo
            record.reference = evidence[:120]
            record.filed_on = file_date
            record.reviewed_by = request.user
            record.save(update_fields=[
                "status", "note", "reference", "filed_on", "reviewed_by", "updated_at",
            ])
        else:
            record = GSTFilingReview.objects.create(
                company=request.user.company, branch=request.user.branch,
                period=selected, status=desired, note=memo,
                reference=evidence[:120], filed_on=file_date,
                reviewed_by=request.user,
            )
        return Response({
            "period": selected, "status": record.status,
            "portalVerified": False,
            "message": "Internal filing evidence recorded; official GST Portal verification unavailable.",
        }, status=201)
