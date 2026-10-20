"""Download an existing SaaS wallet-credit billing statement as a safe A4 PDF.

A PDF is generated on GET from already finalized DB records. It is NOT a GST
tax invoice or rupee payment receipt: credit units have no documented INR value.
"""
from html import escape
from io import BytesIO

from django.http import HttpResponse
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _safe(value):
    return escape(str(value if value is not None else "—"))


def create_saas_credit_statement_pdf(company, invoice):
    """Create a self-contained credit statement; never cause a wallet debit."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title="CubeGears SaaS Credit Statement " + invoice["reference"],
        author="CubeGears",
    )
    base = getSampleStyleSheet()
    title = ParagraphStyle("saas-title", parent=base["Heading1"], fontName="Helvetica-Bold",
                           fontSize=18, leading=22, textColor=colors.HexColor("#1C3B66"))
    heading = ParagraphStyle("saas-heading", parent=base["Heading3"], fontName="Helvetica-Bold",
                             fontSize=10, leading=14, textColor=colors.HexColor("#2458A6"))
    normal = ParagraphStyle("saas-body", parent=base["Normal"], fontSize=9,
                            leading=13, textColor=colors.HexColor("#243247"), wordWrap="CJK")
    small = ParagraphStyle("saas-small", parent=normal, fontSize=8, leading=11,
                           textColor=colors.HexColor("#52647A"))
    right = ParagraphStyle("saas-right", parent=normal, alignment=TA_RIGHT)
    def P(value, style=normal):
        return Paragraph(_safe(value), style)

    def datefmt(value):
        if value is None:
            return "Not recorded"
        if hasattr(value, "strftime"):
            from django.utils import timezone
            return timezone.localtime(value).strftime("%d %b %Y, %I:%M %p")
        return str(value)

    elements = [
        P("CubeGears | SaaS Credit Billing Statement", title),
        Spacer(1, 8),
        P("Reference: " + invoice["reference"]),
        P("Source: " + invoice["source"].title() + " billing engine", small),
        Spacer(1, 14),
        P("Billed workspace", heading),
        P(company.legal_name or company.name),
        P(company.name + " | " + str(company.pk), small),
    ]
    if company.email:
        elements.append(P(company.email, small))
    if company.address:
        elements.append(P(company.address, small))
    elements.extend([
        Spacer(1, 15),
        P("Billing record", heading),
    ])

    metadata = [
        ("Usage period", invoice["period"]),
        ("Created / finalized", datefmt(invoice["issued_at"])),
        ("Due at", datefmt(invoice["due_at"])),
        ("Payment status", invoice["status_label"]),
        ("Settled at", datefmt(invoice["paid_at"])),
    ]
    table = Table([[P(k, small), P(v)] for k, v in metadata], colWidths=[52 * mm, 114 * mm])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#F5F8FC")]),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.extend([table, Spacer(1, 16), P("Credit charges", heading)])
    amounts = []
    if invoice["feature_credits"] is not None:
        amounts.append(("Feature usage", invoice["feature_credits"]))
    if invoice["storage_credits"] is not None:
        amounts.append(("Storage usage", invoice["storage_credits"]))
    if not amounts:
        amounts.append((invoice["category"], invoice["total_credits"]))

    rows = [[P("Description", heading), P("Wallet credits", heading)]]
    rows += [[P(label), P(f"{amount} credits", right)] for label, amount in amounts]
    rows.append([P("Total billed credits", heading), P(f'{invoice["total_credits"]} credits', right)])
    charges = Table(rows, colWidths=[108 * mm, 58 * mm], repeatRows=1)
    charges.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), .8, colors.HexColor("#BCD2ED")),
        ("LINEABOVE", (0, -1), (-1, -1), 1, colors.HexColor("#BCD2ED")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#EEF4FC")),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    elements.extend([
        charges, Spacer(1, 24),
        P("Important: This is a SaaS wallet-credit usage statement based on an existing billing record. "
          "Credits are platform units, not Indian Rupees. This is not a GST tax invoice "
          "or proof of a separate INR payment.", small),
    ])
    doc.build(elements)
    return buffer.getvalue()


def pdf_download_response(company, invoice):
    payload = create_saas_credit_statement_pdf(company, invoice)
    response = HttpResponse(payload, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="cubegears-saas-credit-{invoice["reference"]}.pdf"'
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
