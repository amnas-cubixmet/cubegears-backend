"""Server-generated correlation IDs for frontend/backend diagnostics."""

from uuid import uuid4


class RequestIdMiddleware:
    """Assign an opaque ID per request, never trusting a client-supplied ID."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = uuid4().hex
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        return response
