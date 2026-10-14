"""JWT-aware API paywall. Django control-panel/admin URLs are unaffected.

Enable only after existing tenant access has been reviewed and billing migrated.
"""
from django.conf import settings
from django.http import JsonResponse
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, AuthenticationFailed
from .billing_service import access_state

ALLOWED_SAAS = (
    "/api/v1/saas/plans",
    "/api/v1/saas/plan-access",
    "/api/v1/saas/checkout/",
)

class SaaSPaymentGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.SAAS_PAYMENT_ENFORCEMENT_ENABLED or request.method == "OPTIONS":
            return self.get_response(request)

        path = request.path_info.rstrip("/")
        if not path.startswith("/api/v1/"):
            return self.get_response(request)
        if path.startswith("/api/v1/auth/") or path == "/api/v1/health":
            return self.get_response(request)
        if path in ALLOWED_SAAS[:2] or path.startswith(ALLOWED_SAAS[2]):
            return self.get_response(request)
        if path == "/api/v1/saas/billing" and request.method in ("GET", "HEAD"):
            return self.get_response(request)

        # Authentication remains DRF's responsibility. Here JWT is used only to
        # identify a real authenticated company before enforcing entitlement.
        try:
            authenticated = JWTAuthentication().authenticate(request)
        except (AuthenticationFailed, InvalidToken):
            authenticated = None
        if authenticated:
            user, _token = authenticated
            if user.is_active and user.company_id and not access_state(user.company)["allowed"]:
                return JsonResponse({
                    "code": "SUBSCRIPTION_PAYMENT_REQUIRED",
                    "message": "Please select or renew a paid plan to continue.",
                }, status=402)
        return self.get_response(request)
