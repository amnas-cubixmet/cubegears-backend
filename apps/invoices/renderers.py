"""DRF content negotiation for the invoice PDF action.

The action returns a Django HttpResponse containing a real PDF, so the
renderer is used for Accept negotiation, not for successful PDF generation.
Authentication/validation errors should remain readable JSON responses.
"""
from rest_framework.renderers import BaseRenderer, JSONRenderer


class InvoicePDFRenderer(BaseRenderer):
    media_type = "application/pdf"
    format = "pdf"
    charset = None
    render_style = "binary"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if isinstance(data, (bytes, bytearray, memoryview)):
            return bytes(data)

        # Auth/permission/not-found exceptions still pass through DRF Response.
        # Render them as JSON instead of labeling error details as a PDF.
        if renderer_context:
            response = renderer_context.get("response")
            if response is not None:
                response["Content-Type"] = "application/json"
        return JSONRenderer().render(data)
