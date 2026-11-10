"""Tenant-scoped, permission-gated live workspace search.

No mock data, cache of customer records or unbounded database scans in the UI.
Each resource executes a separately permission-checked, bounded ORM query.
"""
from urllib.parse import urlencode

from django.db.models import Q, TextField
from django.db.models.functions import Cast
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import user_has_permission
from apps.customers.models import Customer
from apps.vehicles.models import Vehicle
from apps.jobs.models import Job
from apps.employees.models import Employee
from apps.inventory.models import Supplier, StockItem, SupplierBill, PurchaseOrder, WorkshopFinancialAccount
from apps.services.models import Service
from apps.expenses.models import Expense
from apps.invoices.models import Invoice
from apps.payments.models import Payment


def _result(group, record, title, subtitle, route, status=""):
    return {
        "id": f"{group.lower().replace(' ', '-')}-{record.pk}",
        "recordId": str(record.pk),
        "group": group,
        "type": group[:-1] if group.endswith("s") else group,
        "title": str(title),
        "subtitle": str(subtitle or ""),
        "status": str(status or ""),
        "route": route,
    }


class WorkspaceSearchView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        raw = request.query_params.get("q", "")
        if not isinstance(raw, str) or len(raw) > 100:
            return Response({"message": "Search query must contain at most 100 characters."}, status=400)
        q = raw.strip()
        if len(q) < 2:
            reply = Response({"query": q, "results": [], "total": 0})
            reply["Cache-Control"] = "private, no-store"
            return reply
        limit_raw = request.query_params.get("limit", "8")
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            return Response({"message": "Limit must be an integer from 1 to 10."}, status=400)
        if not 1 <= limit <= 10:
            return Response({"message": "Limit must be an integer from 1 to 10."}, status=400)

        company_id = request.user.company_id
        if not company_id or not request.user.company.is_active:
            return Response({"message": "An active workshop account is required."}, status=403)
        result = []
        allows = lambda code: user_has_permission(request.user, code)

        if allows("customers.view"):
            rows = Customer.objects.filter(company_id=company_id).filter(
                Q(name__icontains=q) | Q(company_name__icontains=q) |
                Q(phone__icontains=q) | Q(whatsapp__icontains=q) |
                Q(email__icontains=q)
            ).order_by("name", "pk")[:limit]
            result.extend(_result("Customers", c, c.name,
                                  f"{c.company_name or c.phone} · {c.status}",
                                  f"/customers/{c.pk}", c.status) for c in rows)

        if allows("vehicles.view"):
            rows = Vehicle.objects.filter(company_id=company_id).filter(
                Q(registration__icontains=q) | Q(vin__icontains=q) |
                Q(make__icontains=q) | Q(model__icontains=q)
            ).order_by("registration", "pk")[:limit]
            result.extend(_result("Vehicles", v, v.registration,
                                  " ".join(filter(None, [v.make, v.model, v.variant])),
                                  f"/vehicles/{v.pk}", v.status) for v in rows)

        if allows("jobs.view"):
            rows = Job.objects.filter(company_id=company_id).select_related("vehicle").filter(
                Q(job_number__icontains=q) | Q(vehicle__registration__icontains=q) |
                Q(customer__name__icontains=q) | Q(status__icontains=q)
            ).order_by("-created_at", "pk")[:limit]
            result.extend(_result("Job Cards", job, job.job_number,
                                  job.vehicle.registration, f"/jobs/{job.pk}",
                                  job.status) for job in rows)
            # Complaints are stored as existing JSON; cast to text for a bounded
            # SQL match. Never return private complaint descriptions in search.
            complaints = Job.objects.filter(company_id=company_id).annotate(
                complaint_text=Cast("complaints", output_field=TextField())
            ).filter(complaint_text__icontains=q).order_by("-created_at", "pk")[:limit]
            result.extend(_result("Complaints", job, f"Complaints · {job.job_number}",
                                  "Open job complaints", f"/jobs/{job.pk}/complaints")
                          for job in complaints)

        if allows("staff.view"):
            rows = Employee.objects.filter(company_id=company_id).filter(
                Q(name__icontains=q) | Q(employee_code__icontains=q) |
                Q(phone__icontains=q) | Q(designation__icontains=q)
            ).order_by("name", "pk")[:limit]
            result.extend(_result("Staff", staff, staff.name,
                                  f"{staff.employee_code} · {staff.designation}",
                                  f"/staff-management/staff/{staff.pk}", staff.status) for staff in rows)

        if allows("stock.view"):
            suppliers = Supplier.objects.filter(company_id=company_id).filter(
                Q(name__icontains=q) | Q(contact_person__icontains=q) |
                Q(phone__icontains=q) | Q(gstin__icontains=q) |
                Q(email__icontains=q) | Q(address__icontains=q)
            ).order_by("name", "pk")[:limit]
            result.extend(_result("Suppliers", supplier, supplier.name,
                                  supplier.contact_person or "Supplier",
                                  f"/suppliers/{supplier.pk}")
                          for supplier in suppliers)
            parts = StockItem.objects.filter(company_id=company_id).filter(
                Q(name__icontains=q) | Q(sku__icontains=q) |
                Q(barcode__icontains=q) | Q(brand__icontains=q) |
                Q(part_number__icontains=q) | Q(hsn_code__icontains=q)
            ).order_by("name", "pk")[:limit]
            result.extend(_result("Inventory", part, part.name, part.sku,
                                  "/stock?" + urlencode({"search": part.sku or part.name}),
                                  part.status) for part in parts)

        if allows("services.view"):
            services = Service.objects.filter(company_id=company_id).filter(
                Q(name__icontains=q) | Q(code__icontains=q) |
                Q(hsn_sac__icontains=q)
            ).order_by("name", "pk")[:limit]
            result.extend(_result("Services", service, service.name,
                                  service.code or "Workshop service",
                                  f"/services/{service.pk}")
                          for service in services)

        if allows("expenses.view"):
            expenses = Expense.objects.filter(company_id=company_id).filter(
                Q(category__icontains=q) | Q(vendor__icontains=q) |
                Q(reference__icontains=q) | Q(description__icontains=q)
            ).order_by("-date", "pk")[:limit]
            result.extend(_result("Expenses", expense,
                                  expense.vendor or expense.category,
                                  expense.category, f"/expenses/{expense.pk}",
                                  expense.status) for expense in expenses)

        if allows("payments.view"):
            accounts = WorkshopFinancialAccount.objects.filter(company_id=company_id).filter(
                Q(name__icontains=q) | Q(bank_name__icontains=q)
            ).order_by("name", "pk")[:limit]
            result.extend(_result("Accounts", account, account.name,
                                  account.kind, f"/finance/accounts/{account.pk}")
                          for account in accounts)

        # Purchase records are finance-sensitive; stock access alone must not
        # disclose supplier bills, purchase amounts, or payment references.
        if allows("stock.view") and allows("payments.view"):
            bills = SupplierBill.objects.filter(company_id=company_id).select_related(
                "supplier"
            ).filter(
                Q(invoice_number__icontains=q) | Q(supplier__name__icontains=q)
            ).order_by("-invoice_date", "pk")[:limit]
            result.extend(_result("Supplier Bills", bill, bill.invoice_number,
                                  bill.supplier.name, f"/suppliers/{bill.supplier_id}",
                                  bill.status) for bill in bills)
            orders = PurchaseOrder.objects.filter(company_id=company_id).select_related(
                "supplier"
            ).filter(
                Q(number__icontains=q) | Q(supplier__name__icontains=q)
            ).order_by("-created_at", "pk")[:limit]
            result.extend(_result("Purchases", order, order.number,
                                  order.supplier.name, f"/suppliers/{order.supplier_id}",
                                  order.status) for order in orders)

        if allows("invoices.view"):
            rows = Invoice.objects.filter(company_id=company_id).filter(
                Q(number__icontains=q) | Q(customer__name__icontains=q)
            ).order_by("-date", "pk")[:limit]
            result.extend(_result("Invoices", invoice, invoice.number,
                                  invoice.kind, f"/invoices/{invoice.pk}",
                                  invoice.status) for invoice in rows)

        if allows("payments.view"):
            rows = Payment.objects.filter(company_id=company_id).filter(
                Q(reference__icontains=q) | Q(method__icontains=q) |
                Q(customer__name__icontains=q) |
                Q(invoice__number__icontains=q)
            ).order_by("-date", "pk")[:limit]
            result.extend(_result("Payments", payment,
                                  payment.reference or f"Payment {str(payment.pk)[:8]}",
                                  f"{payment.method} · {payment.amount}",
                                  f"/payments/{payment.pk}", payment.status) for payment in rows)

        reply = Response({"query": q, "total": len(result), "results": result})
        reply["Cache-Control"] = "private, no-store"
        return reply
