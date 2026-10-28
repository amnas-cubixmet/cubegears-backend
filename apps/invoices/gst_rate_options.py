"""Workshop GST dropdown presets shared by invoice and E-Way Bill APIs.

Rates shown here are *choices requested for the workshop UI*, not a claim that
all goods/services have these statutory rates. Backend tax arithmetic uses each
line's combined GST percentage: 9% CGST + 9% SGST = 18% total.
The duplicate 'IGST 5%' entry in the UI requirements is intentionally deduped.
Existing invoices/estimates may retain other historic classified rates.
"""

CGST_COMPONENT_RATES = ("2.5", "5", "9", "18")
SGST_COMPONENT_RATES = CGST_COMPONENT_RATES
IGST_RATES = ("5",)


def gst_rate_options():
    """Return select options keyed by the GST jurisdiction mode."""
    return {
        "cgst_sgst": [
            {
                "value": str(int(float(component) * 2)) if float(component) * 2 == int(float(component) * 2) else str(float(component) * 2),
                "label": f"CGST {component}% + SGST {component}% = {float(component) * 2:g}%",
            }
            for component in CGST_COMPONENT_RATES
        ],
        "igst": [{"value": rate, "label": f"IGST {rate}%"} for rate in IGST_RATES],
    }
