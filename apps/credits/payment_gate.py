"""Monthly debt gate: account recovery and read-only storage remain reachable."""
from django.http import JsonResponse
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import AuthenticationFailed
from .monthly_policy import billing_status, request_billing_cache


def recovery_request(path, method):
    if path.startswith('/api/v1/auth/') or path in {'/api/v1/health', '/api/v1/health/'}:
        return True
    if path.startswith('/api/v1/saas/credits/'):
        return True  # DRF still enforces owner-only wallet and purchase permissions.
    if method in ('GET', 'HEAD') and (path == '/api/v1/saas/storage' or path.startswith('/api/v1/saas/storage/history') or path == '/api/v1/saas/storage/events'):
        return True
    return False


class MonthlyPaymentGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = request_billing_cache.set({})
        try:
            return self.handle(request)
        finally:
            request_billing_cache.reset(token)

    def handle(self, request):
        path = request.path_info.rstrip('/')
        if request.method == 'OPTIONS' or not path.startswith('/api/v1/') or recovery_request(path, request.method):
            return self.get_response(request)
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            try:
                auth = JWTAuthentication().authenticate(request)
                user = auth[0] if auth else None
            except AuthenticationFailed:
                user = None
        if user and user.is_active and user.company_id:
            status = billing_status(user.company)
            if status['blocked']:
                return JsonResponse({'code': 'MONTHLY_PAYMENT_OVERDUE',
                    'message': 'Monthly usage payment is overdue. Open SaaS Account to review usage and buy credits.',
                    'billingStatus': status}, status=402)
        return self.get_response(request)
