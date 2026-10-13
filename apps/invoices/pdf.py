"""Create a complete A4 invoice PDF from an authorized, saved invoice record.

No browser print dependency or remote service. Only persisted invoice/payment
data is used so draft form changes never leak into the official document.
"""
from decimal import Decimal
from html import escape
from io import BytesIO

from django.http import HttpResponse
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
)


INK = colors.HexColor("#152437")
MUTED = colors.HexColor("#587085")
BLUE = colors.HexColor("#2263D8")
PALE = colors.HexColor("#EEF4FE")
LINE = colors.HexColor("#DCE5EF")


def _txt(value, default="—"):
    return str(value).strip() if value is not None and str(value).strip() else default


def _p(value, style):
    # Escape all customer-controlled fields; markup must never reach Platypus.
    return Paragraph(escape(_txt(value)).replace("\n", "<br/>"), style)


def _money(value):
    try:
        return "INR {:,.2f}".format(Decimal(value or 0))
    except (TypeError, ValueError, ArithmeticError):
        return "INR 0.00"


def _snapshot(snapshot, fallback):
    # Blank snapshot fields should fall back to the persisted company/contact.
    result = dict(fallback)
    for key, value in (snapshot or {}).items():
        if value is not None and str(value).strip():
            result[key] = value
    return result


def create_invoice_pdf(invoice):
    """Return PDF bytes for one saved, already-authorized invoice."""
    doc_buffer = BytesIO()
    doc = SimpleDocTemplate(
        doc_buffer, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"{invoice.number} - {'Estimate' if invoice.kind == 'estimate' else 'Invoice'}",
        author="CubixGear",
        pageCompression=1,
    )

    base = getSampleStyleSheet()
    styles = {
        "company": ParagraphStyle("cg-company", parent=base["Heading1"], fontName="Helvetica-Bold",
                                  fontSize=16, leading=20, textColor=INK, spaceAfter=7),
        "title": ParagraphStyle("cg-title", parent=base["Heading2"], fontName="Helvetica-Bold",
                                fontSize=17, leading=20, textColor=BLUE, alignment=TA_RIGHT),
        "heading": ParagraphStyle("cg-heading", parent=base["Normal"], fontName="Helvetica-Bold",
                                  fontSize=9, leading=13, spaceAfter=5, textColor=BLUE),
        "body": ParagraphStyle("cg-body", parent=base["Normal"], fontName="Helvetica",
                               fontSize=8.6, leading=12.5, textColor=INK, splitLongWords=True),
        "small": ParagraphStyle("cg-small", parent=base["Normal"], fontName="Helvetica",
                                fontSize=7.5, leading=10.5, textColor=MUTED, splitLongWords=True),
        "thead": ParagraphStyle("cg-thead", parent=base["Normal"], fontName="Helvetica-Bold",
                                fontSize=7.3, leading=10, textColor=colors.white),
        "td": ParagraphStyle("cg-td", parent=base["Normal"], fontName="Helvetica",
                             fontSize=7.8, leading=11, textColor=INK, splitLongWords=True),
        "tdright": ParagraphStyle("cg-tdright", parent=base["Normal"], fontName="Helvetica",
                                  fontSize=7.8, leading=11, textColor=INK, alignment=TA_RIGHT),
        "total": ParagraphStyle("cg-total", parent=base["Normal"], fontName="Helvetica-Bold",
                                fontSize=10.5, leading=14, textColor=BLUE, alignment=TA_RIGHT),
    }
    P = lambda value, name="body": _p(value, styles[name])
    fallback_seller = {
        "name": invoice.company.legal_name or invoice.company.name,
        "address": invoice.company.address,
        "phone": invoice.company.phone,
        "email": invoice.company.email,
        "gstin": invoice.company.gstin,
        "state": invoice.company.state,
        "pincode": invoice.company.pincode,
    }
    seller = _snapshot(invoice.seller_snapshot, fallback_seller)
    customer = _snapshot(invoice.customer_snapshot, {
        "name": invoice.customer.name, "phone": invoice.customer.phone,
        "email": invoice.customer.email, "gstin": invoice.customer.gstin,
        "address": invoice.customer.address,
    })
    vehicle = _snapshot(invoice.vehicle_snapshot, {
        "registration": invoice.vehicle.registration if invoice.vehicle else "",
        "makeModel": " ".join(filter(None, [invoice.vehicle.make, invoice.vehicle.model]))
                     if invoice.vehicle else "",
        "vin": invoice.vehicle.vin if invoice.vehicle else "",
        "odometer": invoice.vehicle.odometer if invoice.vehicle else "",
    })
    transport = invoice.transport or {}
    is_gst = invoice.invoice_type == "gst"
    recipient_type = (invoice.customer_snapshot or {}).get("customerType") or invoice.customer.customer_type
    is_business = recipient_type == "business"
    # Presentation follows the buyer, tax liability follows the supplier.
    # A taxable B2C service bill is still a tax invoice under GST law.
    title = "ESTIMATE / QUOTATION" if invoice.kind == "estimate" else (
        ("TAX INVOICE" if is_gst else "BUSINESS INVOICE") if is_business else
        ("SERVICE BILL / TAX INVOICE" if is_gst else "SERVICE BILL")
    )
    story = []
    header = Table([[
        [P(seller.get("name"), "company"),
         P(", ".join(filter(None, [str(seller.get("address") or ""),
                                   str(seller.get("state") or ""),
                                   str(seller.get("pincode") or "")])), "small"),
         P(f"Phone: {_txt(seller.get('phone'))}  |  Email: {_txt(seller.get('email'))}", "small"),
         P(f"GSTIN: {_txt(seller.get('gstin'))}", "small") if seller.get("gstin") else P("", "small")],
        [P(title, "title"), P(f"{'Invoice No' if is_business else 'Bill No'}: {invoice.number}", "tdright"),
         P(f"Date: {invoice.date:%d %b %Y}", "tdright"),
         P(f"Status: {invoice.status}", "tdright")],
    ]], colWidths=[doc.width * .55, doc.width * .45])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LINEBELOW", (0, 0), (-1, -1), 1, BLUE),
    ]))
    story.extend([header, Spacer(1, 10)])

    def block(heading, vals):
        cells = [P(heading, "heading")]
        for label, value in vals:
            if value not in ("", None):
                cells.append(P(f"{label}: {value}", "small"))
        return cells

    bill_to = block("BILL TO" if is_business else "CUSTOMER", [
        ("Company", customer.get("companyName") if is_business else None),
        ("Name", customer.get("name")),
        ("Phone", customer.get("phone")),
        ("Email", customer.get("email")),
        ("Address", customer.get("address")),
        ("GSTIN", customer.get("gstin") if is_business else None),
        ("Place of supply", customer.get("placeOfSupply")),
    ])
    vehicle_info = block("JOB / VEHICLE", [
        ("Job Card", invoice.job.job_number if invoice.job else invoice.source_job_id),
        ("Registration", vehicle.get("registration")),
        ("Make / Model", vehicle.get("makeModel")),
        ("VIN / Chassis", vehicle.get("vin")),
        ("Odometer", vehicle.get("odometer")),
        ("Advisor", invoice.staff_name),
        ("Transport No.", transport.get("vehicleNo")),
        ("Transporter", transport.get("transporterName")),
    ])
    contacts = Table([[bill_to, vehicle_info]], colWidths=[doc.width / 2] * 2)
    contacts.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE),
        ("BOX", (0, 0), (-1, -1), .6, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 11),
        ("RIGHTPADDING", (0, 0), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 11),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
    ]))
    story.extend([contacts, Spacer(1, 17), P("WORK, SERVICES & PARTS", "heading"), Spacer(1, 5)])

    if is_gst:
        col_widths = [24, doc.width - 24 - 55 - 36 - 49 - 59 - 43 - 66, 55, 36, 49, 59, 43, 66]
        headers = ["#", "Description / HSN", "Type", "Qty", "Unit", "Rate", "GST %", "Amount"]
    else:
        col_widths = [24, doc.width - 24 - 55 - 36 - 49 - 59 - 66, 55, 36, 49, 59, 66]
        headers = ["#", "Description", "Type", "Qty", "Unit", "Rate", "Amount"]
    table_rows = [[P(h, "thead") for h in headers]]
    for idx, item in enumerate(invoice.items.all().order_by("id"), 1):
        line_amount = max(Decimal("0"), item.quantity * item.rate - item.discount)
        description = _txt(item.description)
        if item.code:
            description += f"\nCode: {item.code}"
        if is_gst and item.hsn_sac:
            description += f"\nHSN/SAC: {item.hsn_sac}"
        if item.discount:
            description += f"\nDiscount: {_money(item.discount)}"
        line_cells = [
            P(str(idx), "td"), P(description, "td"),
            P(item.item_type, "td"), P(str(item.quantity), "tdright"),
            P(item.unit, "td"), P(_money(item.rate), "tdright"),
        ]
        if is_gst:
            line_cells.append(P(str(item.tax_rate), "tdright"))
        line_cells.append(P(_money(line_amount), "tdright"))
        table_rows.append(line_cells)
    if len(table_rows) == 1:
        table_rows.append([P("—", "td"), P("No saved invoice items", "td")] + [P("", "td")] * (len(headers) - 2))

    lines_table = Table(table_rows, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    lines_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F9FC")]),
        ("LINEBELOW", (0, 1), (-1, -1), .35, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([lines_table, Spacer(1, 13)])

    sums = [
        ("Taxable amount" if is_gst else "Subtotal (after discounts)", invoice.taxable),
        ("Invoice discount", invoice.discount),
    ]
    if invoice.cgst:
        sums.append(("CGST", invoice.cgst))
    if invoice.sgst:
        sums.append(("SGST", invoice.sgst))
    if invoice.igst:
        sums.append(("IGST", invoice.igst))
    if invoice.adjustment:
        sums.append(("Adjustment", invoice.adjustment))
    sums.extend([("TOTAL", invoice.total), ("Paid", invoice.paid), ("Balance due", invoice.balance)])
    total_rows = [[P(label, "total" if label == "TOTAL" else "body"),
                   P(_money(value), "total" if label == "TOTAL" else "tdright")]
                  for label, value in sums]
    totals = Table(total_rows, colWidths=[doc.width * .40, doc.width * .22], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("LINEABOVE", (0, len(total_rows)-3), (-1, len(total_rows)-3), 1, BLUE),
        ("BACKGROUND", (0, len(total_rows)-3), (-1, len(total_rows)-3), PALE),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([totals, Spacer(1, 15)])

    payment_rows = list(invoice.payments.filter(status="Completed").order_by("date", "created_at"))
    if payment_rows:
        story.append(P("PAYMENT HISTORY", "heading"))
        for paid in payment_rows:
            story.append(P(
                f"{paid.date:%d %b %Y} · {paid.method} · {_money(paid.amount)}"
                + (f" · Ref: {paid.reference}" if paid.reference else ""),
                "small"
            ))
        story.append(Spacer(1, 10))
    extra = [
        ("Payment method", invoice.payment_mode),
        ("Payment terms", invoice.payment_terms),
        ("Notes", invoice.notes),
        ("Terms & conditions", invoice.terms_conditions),
    ]
    for label, value in extra:
        if value:
            story.append(P(label.upper(), "heading"))
            story.append(P(value, "small"))
            story.append(Spacer(1, 7))

    def footer(canvas, report):
        canvas.saveState()
        w, _ = A4
        canvas.setStrokeColor(LINE)
        canvas.line(15 * mm, 15 * mm, w - 15 * mm, 15 * mm)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(15 * mm, 11 * mm, f"{invoice.number}  |  Generated from saved invoice")
        canvas.drawRightString(w - 15 * mm, 11 * mm, f"Page {report.page}")
        if invoice.status in ("Draft", "Cancelled"):
            canvas.translate(w / 2, A4[1] / 2)
            canvas.rotate(38)
            canvas.setFillColor(colors.HexColor("#E5EAF2"))
            canvas.setFont("Helvetica-Bold", 48)
            canvas.drawCentredString(0, 0, invoice.status.upper())
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return doc_buffer.getvalue()


def pdf_response(invoice):
    pdf = create_invoice_pdf(invoice)
    response = HttpResponse(pdf, content_type="application/pdf")
    # Never cache customer invoices in shared intermediaries.
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    # Sanitize filename but include the saved invoice number.
    safe_number = "".join(c if c.isascii() and (c.isalnum() or c in "-_") else "-" for c in invoice.number)[:80]
    response["Content-Disposition"] = f'attachment; filename="{safe_number or "invoice"}.pdf"'
    return response
