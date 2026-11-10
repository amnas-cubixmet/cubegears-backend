"""GSTR APIs: preparation only, no portal filing or financial mutations."""
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal

from django.http import HttpResponse
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import RolePermission
from .services import amount, money, parse_period, prepare


def _period(request):
    return parse_period(request.query_params.get("period"))


class GSTRReportView(APIView):
    permission_classes = [RolePermission]
    permission_code = "gstr.view"

    def get(self, request, section="overview"):
        data = prepare(request.user.company, _period(request))
        if section == "overview":
            return Response(data)
        if section == "gstr-1":
            return Response({**data["gstr1"], "period": data["period"],
                             "issues": data["issues"], "warnings": data["warnings"]})
        if section == "gstr-3b":
            return Response({**data["gstr3b"], "period": data["period"],
                             "issues": data["issues"], "warnings": data["warnings"]})
        if section == "gstr-2b":
            return Response({**data["gstr2b"], "period": data["period"],
                             "warnings": data["warnings"]})
        if section == "hsn-summary":
            return Response({**data["hsn"], "period": data["period"],
                             "issues": data["issues"]})
        if section == "filing-status":
            return Response({**data["filing"], "period": data["period"],
                             "message": "Official filing verification is not connected."})
        raise ValidationError({"section": "Unknown GSTR report."})


def _number(value):
    return str(value or "").strip().upper().replace(" ", "")


def _official_2b_rows(payload, expected_gstin, expected_period):
    if not isinstance(payload, dict):
        raise ValidationError({"file": "Expected a GST Portal GSTR-2B JSON object."})
    if isinstance(payload.get("data"), dict):
        payload = payload["data"]
    declared_gstin = _number(payload.get("gstin"))
    if not declared_gstin or declared_gstin != _number(expected_gstin):
        raise ValidationError({"file": "GSTR-2B GSTIN does not match this workshop."})
    period_value = _number(payload.get("rtnprd"))
    expected = expected_period[5:] + expected_period[:4]
    if period_value != expected:
        raise ValidationError({"file": "GSTR-2B return period does not match the selected month."})
    document_data = payload.get("docdata")
    if not isinstance(document_data, dict) or not isinstance(document_data.get("b2b"), list):
        raise ValidationError({"file": "Unsupported 2B format: docdata.b2b must be an array."})
    unsupported = [name for name, value in document_data.items()
                   if name != "b2b" and value]
    rows = []
    for supplier in document_data["b2b"]:
        if not isinstance(supplier, dict):
            raise ValidationError({"file": "Invalid B2B supplier structure."})
        gstin = _number(supplier.get("ctin"))
        if not re.fullmatch(r"[0-9]{2}[A-Z0-9]{13}", gstin):
            raise ValidationError({"file": "Invalid supplier GSTIN in 2B statement."})
        invoices = supplier.get("inv")
        if not isinstance(invoices, list):
            raise ValidationError({"file": "Invalid B2B invoice list in 2B statement."})
        for inv in invoices:
            if not isinstance(inv, dict) or not _number(inv.get("inum")):
                raise ValidationError({"file": "Invalid invoice structure in GSTR-2B."})
            lines = inv.get("itms") or []
            if not isinstance(lines, list):
                raise ValidationError({"file": "Invalid tax line structure in GSTR-2B."})
            totals = {"taxable": Decimal("0"), "igst": Decimal("0"),
                      "cgst": Decimal("0"), "sgst": Decimal("0"), "cess": Decimal("0")}
            for line in lines:
                fields = line.get("itm_det") if isinstance(line, dict) else None
                if not isinstance(fields, dict):
                    raise ValidationError({"file": "Unrecognized GSTR-2B invoice tax line."})
                for key, source in (("taxable", "txval"), ("igst", "iamt"),
                                    ("cgst", "camt"), ("sgst", "samt"), ("cess", "csamt")):
                    totals[key] += amount(fields.get(source))
            rows.append({
                "supplierGSTIN": gstin, "number": _number(inv["inum"]),
                "date": str(inv.get("idt") or "").strip(),
                **{k: money(v) for k, v in totals.items()},
                "itcAvailableOnPortal": inv.get("itcavl") or supplier.get("itcavl") or "",
            })
            if len(rows) > 10000:
                raise ValidationError({"file": "Limit is 10,000 B2B invoices per upload."})
    return rows, unsupported


def _date_key(value):
    raw = str(value or "").strip()
    for pattern in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
    return None


def _reconcile(books, statement):
    # Match on normalized supplier GSTIN + invoice number. Date and amounts
    # remain independent comparisons to avoid silently accepting discrepancies.
    groups = {}
    for row in books:
        key = (_number(row["supplierGSTIN"]), _number(row["number"]))
        groups.setdefault(key, []).append(row)
    occurrences = Counter((_number(row["supplierGSTIN"]), _number(row["number"])) for row in statement)
    matched_keys = set()
    results = []
    for row in statement:
        key = (_number(row["supplierGSTIN"]), _number(row["number"]))
        candidates = groups.get(key, [])
        if occurrences[key] > 1 or len(candidates) > 1:
            state = "Duplicate"
        elif not candidates:
            state = "Missing in Books"
        else:
            bill = candidates[0]
            matched_keys.add(key)
            if not bill["taxVerified"] or not _date_key(row["date"]):
                state = "Requires Review"
            elif _date_key(row["date"]) != _date_key(bill["date"]):
                state = "Requires Review"
            elif abs(amount(row["taxable"]) - amount(bill["taxable"])) > Decimal("0.01"):
                state = "Amount Mismatch"
            elif abs(sum((amount(row[k]) for k in ("cgst", "sgst", "igst")), Decimal("0")) -
                     amount(bill["tax"])) > Decimal("0.01"):
                state = "Tax Mismatch"
            elif any(abs(amount(row[k]) - amount(bill[k])) > Decimal("0.01")
                     for k in ("cgst", "sgst", "igst")):
                state = "Tax Mismatch"
            else:
                state = "Matched"
        results.append({**row, "status": state,
                        "bookId": str(candidates[0]["id"]) if len(candidates) == 1 else None})
    for key, bills in groups.items():
        if key in matched_keys or key in occurrences:
            continue
        for bill in bills:
            results.append({**bill, "status": "Missing in GSTR-2B",
                            "bookId": bill["id"]})
    return results


class GSTR2BReconcileView(APIView):
    permission_classes = [RolePermission]
    # Reading previously saved matching evidence is read-only; uploading
    # or saving new evidence requires the separate reconciliation permission.
    permission_map = {"GET": ["gstr.view"], "POST": ["gstr.reconcile"]}
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        """Reopen an audited, previously saved 2B reconciliation after refresh."""
        from django.shortcuts import get_object_or_404
        from .models import GSTR2BImport

        selected = _period(request)
        import_id = request.query_params.get("importId")
        if not import_id:
            raise ValidationError({"importId": "Select a saved GSTR-2B import."})
        batch = get_object_or_404(
            GSTR2BImport, pk=import_id, company=request.user.company,
            period=selected["period"],
        )
        rows = batch.rows if isinstance(batch.rows, list) else []
        return Response({
            "period": selected["period"], "importId": str(batch.pk),
            "saved": True, "rows": rows,
            "counts": dict(Counter(row.get("status", "Requires Review") for row in rows)),
            "unsupportedSections": batch.unsupported_sections,
            "importedAt": batch.created_at.isoformat(),
            "notice": (
                "Persisted matching evidence only. Supplier bills and cash "
                "balances are unchanged; ITC needs separate review."
            ),
        })

    def post(self, request):
        period = _period(request)
        company = request.user.company
        if not company.gstin:
            raise ValidationError({"file": "Register the workshop GSTIN first."})
        uploaded = request.FILES.get("file")
        if not uploaded or not uploaded.name.lower().endswith(".json"):
            raise ValidationError({"file": "Select the official GSTR-2B JSON download."})
        if uploaded.size > 5 * 1024 * 1024:
            raise ValidationError({"file": "Maximum JSON upload size is 5 MB."})
        raw = uploaded.read()
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeError):
            raise ValidationError({"file": "Invalid UTF-8 JSON file."})
        statement, unsupported = _official_2b_rows(payload, company.gstin, period["period"])
        from .services import inward_rows
        books = inward_rows(company)[0]
        results = _reconcile(books, statement)
        import_id = None
        if request.query_params.get("save") == "1":
            from django.db import transaction
            from apps.companies.models import Company
            from .models import GSTR2BImport
            # A persisted match is only documentary evidence, never an ITC claim.
            with transaction.atomic():
                Company.objects.select_for_update().get(pk=company.pk)
                saved, _ = GSTR2BImport.objects.get_or_create(
                    company=company, period=period["period"],
                    fingerprint=hashlib.sha256(raw).hexdigest(),
                    defaults={
                        "branch": request.user.branch, "rows": results,
                        "unsupported_sections": unsupported,
                        "imported_by": request.user,
                    },
                )
                import_id = str(saved.pk)
        return Response({
            "period": period["period"], "rows": results,
            "importId": import_id, "saved": bool(import_id),
            "counts": dict(Counter(r["status"] for r in results)),
            "unsupportedSections": unsupported,
            "notice": (
                "2B reconciliation results are saved as evidence only when Save is selected. "
                "ITC eligibility requires a separate authorized review, including tax exclusions."
            ),
        })


def _safe_cell(value):
    # Spreadsheet CSV formula injection protection for user-controlled labels.
    text = str(value if value is not None else "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else text


class GSTRCSVExportView(APIView):
    permission_classes = [RolePermission]
    permission_code = "gstr.export"

    def get(self, request):
        data = prepare(request.user.company, _period(request))
        report = request.query_params.get("report", "gstr1")
        if report == "gstr1":
            fields = ["number", "date", "customer", "customerGSTIN", "placeOfSupply",
                      "classification", "taxable", "cgst", "sgst", "igst", "total"]
            rows = data["gstr1"]["rows"]
        elif report == "hsn":
            fields = ["hsnSac", "description", "unit", "rate", "classification",
                      "quantity", "taxablePreview", "cgst", "sgst", "igst"]
            rows = data["hsn"]["rows"]
        elif report == "credits":
            fields = ["number", "date", "invoiceNumber", "reason",
                      "taxable", "cgst", "sgst", "igst", "total"]
            rows = data["gstr1"]["creditNotes"]
        elif report == "gstr3b":
            fields = ["metric", "amount"]
            rows = [
                {"metric": key, "amount": value}
                for key, value in data["gstr3b"].items()
                if key in {
                    "outwardTaxable", "outputCGST", "outputSGST",
                    "outputIGST", "eligibleITC", "reversedITC",
                    "indicativeGapBeforeUtilization",
                }
            ]
        elif report == "itc":
            from .models import GSTInputTaxClaim
            fields = ["invoiceNumber", "period", "cgst", "sgst", "igst", "reason"]
            claims = GSTInputTaxClaim.objects.filter(
                company=request.user.company, period=data["period"],
            ).select_related("bill")
            rows = [{
                "invoiceNumber": row.bill.invoice_number,
                "period": row.period, "cgst": str(row.cgst),
                "sgst": str(row.sgst), "igst": str(row.igst),
                "reason": row.eligibility_reason,
            } for row in claims]
        else:
            raise ValidationError({
                "report": "Select gstr1, gstr3b, credits, itc or hsn preparation CSV.",
            })
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="cubegears-{report}-{data["period"]}-PREPARATION.csv"')
        writer = csv.writer(response)
        writer.writerow(["CubeGears GST preparation only", "NOT PORTAL-COMPATIBLE", data["period"]])
        writer.writerow(["Workshop", _safe_cell(data["company"]["name"]),
                         "GSTIN", _safe_cell(data["company"]["gstin"])])
        writer.writerow(fields)
        for row in rows:
            writer.writerow([_safe_cell(row.get(field)) for field in fields])
        return response
