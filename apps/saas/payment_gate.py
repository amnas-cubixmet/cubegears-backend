"""Server-side enforcement for all authenticated tenant APIs.

Activation is controlled by PaymentPolicy. Platform staff and anonymous public
endpoints are not redirected. Frontend route guards are only UX, not security.
"""
from django.http import JsonResponse
from rest_framework_simplejwt.authentication import JWTAuthentication
from .payment_models import PaymentPolicy
from .payment_service import has_paid_access

EXEMPT = (
    "/api/v1/auth/", "/api/v1/saas/plans",
    "/api/v1/saas/payments/", "/api/v1/saas/plan-access",
    "/api/v1/saas/billing", "/api/v1/health/",
)

class SaaSPaymentGateMiddleware:
    def __init__(self, get_response):
        self.get_response=get_response

    def __call__(self, request):
        path=request.path_info
        if not path.startswith("/api/v1/") or any(path.startswith(prefix) for prefix in EXEMPT):
            return self.get_response(request)
        policy=PaymentPolicy.current()
        if not policy.enforce_paywall:
            return self.get_response(request)
        try:
            authenticated=JWTAuthentication().authenticate(request)
        except Exception:
            authenticated=None
        user=authenticated[0] if authenticated else request.user
        if not user or not user.is_authenticated or not user.is_active or not user.company_id:
            return self.get_response(request)
        if not has_paid_access(user.company):
            return JsonResponse({
                "code":"SUBSCRIPTION_REQUIRED",
                "message":"Select a plan and complete payment to use CubeGear.",
                "next":"/plans",
            },status=402)
        return self.get_response(request)
