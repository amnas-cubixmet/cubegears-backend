"""One account-aware destination for setup, reset and magic-link emails."""
from urllib.parse import urlencode
from django.conf import settings
from django.urls import reverse


def is_platform_account(user):
    return bool(user and not user.company_id and (user.is_staff or user.is_superuser))


def password_action_url(request, user, uid, token, *, setup=False):
    if is_platform_account(user):
        route = 'password-setup' if setup else 'password-reset'
        return request.build_absolute_uri(reverse(f'panel:{route}', kwargs={'uid': uid, 'token': token}))
    route = 'setup-password' if setup else 'reset-password'
    query = urlencode({'uid': uid, 'token': token, 'email': user.email})
    return f"{settings.FRONTEND_URL.rstrip('/')}/{route}?{query}"


def magic_action_url(request, user, token):
    if is_platform_account(user):
        return request.build_absolute_uri(reverse('panel:magic-verify', kwargs={'token': token}))
    return f"{settings.FRONTEND_URL.rstrip('/')}/magic-link/verify?{urlencode({'token': token})}"
