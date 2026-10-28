"""GST rate dropdown presets for workshop billing.

Selectable labels are distinct. A CGST/SGST choice expresses its corresponding
half of a paired in-state rate; Django stores the *total* percentage for each
line (e.g. CGST 9% => total 18%, applied as CGST 9% + SGST 9%). IGST 18%
stores total 18%. Tax jurisdiction is still determined from legal states.
These are UI presets only, not a claim about statutory rates for every HSN/SAC.
"""
from decimal import Decimal

CGST_COMPONENT_RATES = ("2.5", "5", "9", "18")
SGST_COMPONENT_RATES = CGST_COMPONENT_RATES
IGST_RATES = ("5", "18")


def gst_rate_options():
    """Exactly 10 visibly distinct options in CGST, SGST, IGST order."""
    options = []
    for component, rates in (("cgst", CGST_COMPONENT_RATES),
                             ("sgst", SGST_COMPONENT_RATES)):
        for rate in rates:
            combined = Decimal(rate) * 2
            options.append({
                "value": f"{component}:{rate}",
                "label": f"{component.upper()} {rate}%",
                "totalRate": format(combined, "f").rstrip("0").rstrip(".")
                             if "." in format(combined, "f") else format(combined, "f"),
                "taxMode": "cgst_sgst",
            })
    for rate in IGST_RATES:
        options.append({
            "value": f"igst:{rate}",
            "label": f"IGST {rate}%",
            "totalRate": rate,
            "taxMode": "igst",
        })
    return options
