"""Read-only Job Card to invoice *suggestions*, never a financial posting.

Values originate from actual recorded job charges. Do not conflate purchase
costs, payroll, pending supplier quotes, or an approved estimate with income.
These lines are intentionally not linked as new invoice stock issues: a job
issue has already decremented inventory and a later invoice must not do it again.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from apps.jobs.models import OutsideLabourCharge

CENT = Decimal("0.01")


def amount(value):
    try:
        result = Decimal(str(value if value is not None else ""))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() and result >= 0 else None


def suggested_job_invoice(job):
    registered = bool(str(job.company.gstin or "").strip())
    items, notices = [], []
    seen_stock = set()

    def append(kind, key, description, quantity, rate, hsn="", tax=None, unit="PCS", code=""):
        qty, price = amount(quantity), amount(rate)
        if not description or qty is None or price is None or qty <= 0:
            notices.append(f"{kind} {key}: check its recorded selling quantity and rate.")
            return
        unit_name = str(unit or "PCS").strip().upper()
        unit_name = {"PIECE": "PCS", "PIECES": "PCS", "JOB": "JOB",
                     "HOUR": "HRS", "HOURS": "HRS", "NUMBER": "NOS"}.get(unit_name, unit_name)
        supported = {"PCS", "NOS", "SET", "KG", "G", "LTR", "ML", "MTR", "CM", "BOX", "PACK", "PAIR",
                     "HRS", "MIN", "DAY", "JOB", "ROLL", "BOTTLE", "BAG", "TON"}
        if unit_name not in supported:
            notices.append("Review the invoice unit for " + str(description)[:90] + ".")
            unit_name = "PCS"

        # An already-issued JobPart must be a descriptive line, not an invoice
        # Stock Part reference; the latter would issue inventory a second time.
        invoice_type = "Custom Item" if kind == "Stock Part" else kind
        items.append({
            "id": f"job-{kind}-{key}", "type": invoice_type,
            "description": str(description).strip()[:250], "code": str(code or "")[:80],
            "qty": str(qty), "rate": str(price.quantize(CENT, rounding=ROUND_HALF_UP)),
            "unit": unit_name, "hsnCode": str(hsn or "").strip()[:30],
            "taxRate": str(tax) if registered and tax is not None and amount(tax) is not None
                else ("" if registered else "0"),
            "discount": "0", "inventoryId": "",
        })

    def item_rate(item):
        return getattr(item, "tax", None), getattr(item, "hsn_code", "") or ""

    # JobPart captures confirmed issued quantities and customer selling rates.
    for part in job.parts.filter(issued=True).select_related("item").order_by("created_at"):
        stock = part.item
        if stock.company_id != job.company_id:
            continue
        seen_stock.add(str(stock.pk))
        tax, hsn = item_rate(stock)
        append("Stock Part", str(part.pk), stock.name, part.quantity,
               part.unit_price, hsn, tax, stock.unit, stock.sku)

    # The other supported issue path stores signed issue/return events in
    # parts_workflow JSON and StockMovement, but not a JobPart row.
    workflow = job.parts_workflow or {}
    for row in workflow.get("lines") or []:
        if not isinstance(row, dict) or not row.get("partId"):
            continue
        if str(row["partId"]) in seen_stock:
            # Never bill a single stock item again through both issue paths.
            continue
        qty = sum((amount(x.get("qty")) or Decimal("0")
                   for x in row.get("issues", []) if isinstance(x, dict)), Decimal("0"))
        qty -= sum((amount(x.get("qty")) or Decimal("0")
                    for x in row.get("returns", []) if isinstance(x, dict)), Decimal("0"))
        if qty <= 0:
            continue
        from apps.inventory.models import StockItem
        stock = StockItem.objects.filter(company=job.company, pk=row["partId"]).first()
        if stock is None:
            notices.append("A recorded stock issue no longer matches an inventory item.")
            continue
        tax, hsn = item_rate(stock)
        append("Stock Part", str(row.get("id") or stock.pk),
               row.get("partName") or stock.name, qty, row.get("sellingPrice"),
               hsn, tax, stock.unit, stock.sku)

    for row in workflow.get("outsideParts") or []:
        if not isinstance(row, dict) or row.get("status", "Purchased") != "Purchased":
            continue
        if row.get("billable") is False:
            continue
        selling = amount(row.get("sellingPrice"))
        if selling is None or selling <= 0:
            notices.append("Outside purchase needs an explicitly recorded customer selling rate before billing.")
            continue
        append("Outside Purchase", str(row.get("id") or len(items)),
               row.get("partName") or row.get("name"), row.get("quantity"),
               selling, row.get("hsnCode") or row.get("hsnSac"), row.get("taxRate"))

    for labour in OutsideLabourCharge.objects.filter(
        company=job.company, job=job
    ).order_by("created_at"):
        if labour.customer_charge <= 0:
            continue
        append("Labour", str(labour.pk), labour.work_description or "Outside service",
               1, labour.customer_charge)

    for row in job.work or []:
        if not isinstance(row, dict):
            continue
        # Explicit customer-billable records only. Never import mechanic wages.
        if row.get("billable") is not True:
            continue
        append("Labour", str(row.get("id") or len(items)),
               row.get("service") or row.get("description"),
               row.get("quantity", 1),
               next((row[key] for key in ("customerRate", "unitPrice", "customerCharge")
                     if row.get(key) is not None), None),
               row.get("hsnSac"), row.get("taxRate"), row.get("unit") or "Job")

    if seen_stock or any(row.get("issues") for row in workflow.get("lines") or [] if isinstance(row, dict)):
        notices.append("Previously issued stock is shown as descriptive invoice items. It will not be deducted again.")
    if registered and any(not row["hsnCode"] or row["taxRate"] == "" for row in items):
        notices.append("Verify HSN/SAC and applicable GST for every drafted item before saving.")
    if not items:
        notices.append("No verified billable Job Card items. Add customer charges manually; no invoice was created.")

    vehicle = job.vehicle
    customer = job.customer
    return {
        "jobId": str(job.pk), "jobNumber": job.job_number,
        "customer": {
            "name": customer.name, "phone": customer.phone or "",
            "address": customer.address or "", "gstin": customer.gstin or "",
            "state": customer.state or "",
        },
        "vehicle": {
            "registration": vehicle.registration,
            "makeModel": " ".join(filter(None, [vehicle.make, vehicle.model])),
            "vin": vehicle.vin or "", "odometer": str(job.odometer or ""),
        },
        "items": items, "warnings": notices,
        "stockAlreadyIssued": True, "created": False, "finalized": False,
    }
