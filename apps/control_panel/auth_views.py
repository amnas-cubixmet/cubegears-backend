"""SaaS Control Panel session login, recovery, and one-time magic links."""
import hashlib

from django import forms
from django.contrib.auth import login
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from apps.accounts.email import queue_email
from apps.accounts.models import MagicLink, User
from .permissions import landing_route


class EmailRequestForm(forms.Form):
    email = forms.EmailField(
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "admin@company.com"}),
    )

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


def _limited(request, email, action):
    # The deployment should configure a SHARED Django cache when running more
    # than one worker, otherwise the default local-memory limiter is per worker.
    ip = request.META.get("REMOTE_ADDR", "")
    digest = hashlib.sha256(f"{action}:{ip}:{email}".encode()).hexdigest()
    key = "panel-auth:" + digest
    if cache.add(key, 1, timeout=3600):
        return False
    try:
        count = cache.incr(key)
    except ValueError:
        return False
    return count > 5


def _panel_user(email):
    user = User.objects.select_related("role", "company").filter(
        email__iexact=email, is_active=True,
    ).first()
    return user if user and landing_route(user) else None


def _uid_user(uid):
    try:
        pk = urlsafe_base64_decode(uid).decode()
        user = User.objects.select_related("role", "company").get(pk=pk, is_active=True)
    except (ValueError, TypeError, UnicodeDecodeError, User.DoesNotExist):
        return None
    return user if landing_route(user) else None


def _auth_page(request, title, description, *, form=None, mode=None, success=False):
    response = render(request, "control_panel/auth_action.html", {
        "title": title, "description": description, "form": form,
        "mode": mode, "success": success,
    })
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "no-store"
    return response


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def panel_forgot_password(request):
    form = EmailRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        if not _limited(request, email, "forgot"):
            user = _panel_user(email)
            if user:
                uid = urlsafe_base64_encode(force_bytes(user.pk))
                token = default_token_generator.make_token(user)
                url = request.build_absolute_uri(reverse(
                    "panel:password-reset", kwargs={"uid": uid, "token": token},
                ))
                queue_email(
                    "Reset your CubixGear Control Panel password",
                    "Use this secure link to choose a new password for your platform account.",
                    [user.email], action_url=url, action_label="Reset password",
                )
        return _auth_page(request, "Check your email",
                          "If a Control Panel account exists, a password reset link has been sent.",
                          success=True)
    return _auth_page(request, "Reset your password",
                      "Enter your administrator email to request a secure reset link.",
                      form=form, mode="email")


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def panel_magic_request(request):
    form = EmailRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        if not _limited(request, email, "magic"):
            user = _panel_user(email)
            if user:
                token = MagicLink.issue(user)
                url = request.build_absolute_uri(reverse(
                    "panel:magic-verify", kwargs={"token": token},
                ))
                queue_email(
                    "Sign in to CubixGear Control Panel",
                    "This is a single-use sign-in link. It expires in a short time.",
                    [user.email], action_url=url, action_label="Sign in securely",
                )
        return _auth_page(request, "Check your email",
                          "If a Control Panel account exists, a secure sign-in link has been sent.",
                          success=True)
    return _auth_page(request, "Email me a login link",
                      "Sign in without typing your password. The emailed link works once.",
                      form=form, mode="email")


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def panel_magic_verify(request, token):
    if request.method == "POST":
        user = MagicLink.consume(token)
        if user and landing_route(user):
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect("panel:" + landing_route(user))
        return _auth_page(request, "Link unavailable",
                          "This login link is invalid, expired, or already used. Request another one.",
                          mode="invalid")
    # GET must not consume a token: email security scanners routinely prefetch
    # links. JS submits a CSRF-protected POST for near-instant sign-in.
    return render(request, "control_panel/magic_verify.html", {"token": token}, status=200)


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def panel_password_action(request, uid, token, *, setup=False):
    user = _uid_user(uid)
    if not user or not default_token_generator.check_token(user, token):
        return _auth_page(request, "Link unavailable",
                          "The password link is invalid or expired. Please request another one.",
                          mode="invalid")
    if setup and user.has_usable_password():
        return _auth_page(request, "Account already set up",
                          "This account already has a password. Use Forgot Password to reset it.",
                          mode="invalid")

    form = SetPasswordForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        saved_user = form.save()
        saved_user.email_verified = True
        saved_user.save(update_fields=["email_verified"])
        # Previously sent magic links should not survive a password reset.
        from django.utils import timezone
        MagicLink.objects.filter(user=saved_user, used_at__isnull=True).update(used_at=timezone.now())
        if setup:
            login(request, saved_user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect("panel:" + landing_route(saved_user))
        return redirect("panel:login")

    return _auth_page(request, "Create your password" if setup else "Choose a new password",
                      "Use a strong, unique password for your Control Panel account.",
                      form=form, mode="password")
