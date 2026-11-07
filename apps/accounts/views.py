from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.text import slugify
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.throttling import AnonRateThrottle
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.views import TokenRefreshView

from .models import MagicLink
from .staff_setup import issue_invitation, invitation_url, resolve_invitation, consume_invitation
from .email import queue_email
from .account_links import is_platform_account, password_action_url, magic_action_url
from .serializers import (
    ChangePasswordSerializer,
    ForgotPasswordSerializer,
    LoginSerializer,
    user_can_sign_in,
    MagicLinkRequestSerializer,
    MagicLinkVerifySerializer,
    ResetPasswordSerializer,
    PublicWorkshopSignupSerializer,
    UserSerializer,
)

from apps.branches.models import Branch
from apps.companies.models import Company
from apps.employees.services import resolve_employee_for_user
from apps.roles.models import Role

User = get_user_model()


class PublicSignupRateThrottle(AnonRateThrottle):
    rate = "5/hour"


class LoginRateThrottle(AnonRateThrottle):
    scope = "workshop_login"
    rate = "10/minute"


class TokenRefreshRateThrottle(AnonRateThrottle):
    # Refreshing a valid session must not exhaust the interactive login limit.
    scope = "workshop_token_refresh"
    rate = "60/minute"


class RecoveryRateThrottle(AnonRateThrottle):
    rate = "5/hour"



def issue_tokens(user):
    refresh = RefreshToken.for_user(user)
    return {
        "token": str(refresh.access_token),
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": UserSerializer(user).data,
    }

class LoginView(APIView):
    # Public auth routes must ignore stale Authorization headers from clients.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        return Response(issue_tokens(serializer.validated_data["user"]))

class SecureTokenRefreshView(TokenRefreshView):
    """Reject refreshes for disabled workshops, revoked roles or inactive staff.

    A stored refresh JWT must never revive a staff login after access is
    disabled. SimpleJWT still performs its usual revocation and rotation checks.
    """

    # A valid refresh can repair an expired access token. Never authenticate
    # the stale bearer header before validating the supplied refresh.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [TokenRefreshRateThrottle]

    def post(self, request, *args, **kwargs):
        raw = request.data.get("refresh")
        if not isinstance(raw, str) or not 0 < len(raw) <= 8192:
            return Response({"detail": "Invalid or expired session."}, status=401)
        try:
            refresh = RefreshToken(raw)
            user = User.objects.select_related("company", "role").filter(
                pk=refresh.get("user_id")
            ).first()
        except (TokenError, ValueError, TypeError):
            return Response({"detail": "Invalid or expired session."}, status=401)
        if not user or not user.has_usable_password() or not user_can_sign_in(user):
            return Response({"detail": "Account access is no longer active."}, status=401)
        return super().post(request, *args, **kwargs)


class LogoutView(APIView):
    # A client must be able to end a session after its access JWT expires,
    # or after the workshop/role was deactivated. Bearer authentication would
    # otherwise reject logout with HTTP 401 before the refresh is revoked.
    # Possession of the refresh JWT is sufficient to blacklist that JWT.
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        refresh_token = request.data.get("refresh")
        if isinstance(refresh_token, str) and 0 < len(refresh_token) <= 8192:
            try:
                RefreshToken(refresh_token).blacklist()
            except (TokenError, ValueError, TypeError):
                # Logout is idempotent even for expired/already-revoked tokens.
                pass
        # The caller must also remove its stored access/refresh tokens.
        return Response({"message": "Logged out successfully."})

class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if not user_can_sign_in(user):
            return Response({"detail": "Account access is no longer active."}, status=403)
        return Response(UserSerializer(user).data)

class ForgotPasswordView(APIView):
    # Public auth routes must ignore stale Authorization headers from clients.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [RecoveryRateThrottle]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        # Staff password reset is now administrator-mediated manual sharing.
        # The public endpoint remains non-enumerating: no user lookup, token,
        # Celery task, Meta API request or staff email is initiated here.
        # Authorized admins can use POST /employees/<id>/invite to prepare
        # a one-use reset URL for a staff member who already has a password.
        generic = {
            "message": (
                "If an eligible staff account exists, ask the workshop administrator "
                "for a secure password reset link."
            )
        }
        if data.get("mobile"):
            return Response(generic)

        # Preserve legacy owner/platform email recovery. Staff onboarding and
        # linked employee accounts recover through workshop-owned WhatsApp.
        user = User.objects.filter(email__iexact=data["email"], is_active=True).first()
        from apps.accounts.permissions import is_company_owner
        if user and (is_platform_account(user) or is_company_owner(user)):
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_url = password_action_url(request, user, uid, token)
            queue_email(
                subject="Reset your CubeGears password",
                message="A request was received to reset the password for your CubeGears account. This link expires according to the account security policy.",
                recipient_list=[user.email],
                action_url=reset_url, action_label="Reset password",
            )
        return Response(generic)

class ResetPasswordView(APIView):
    # Public auth routes must ignore stale Authorization headers from clients.
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            if serializer.validated_data.get("uid"):
                user_id = urlsafe_base64_decode(serializer.validated_data["uid"]).decode()
                user = User.objects.get(pk=user_id, is_active=True)
            else:
                user = User.objects.get(email__iexact=serializer.validated_data["email"], is_active=True)
        except Exception:
            return Response({"message": "Invalid or expired reset link."}, status=status.HTTP_400_BAD_REQUEST)

        if not default_token_generator.check_token(user, serializer.validated_data["token"]):
            return Response({"message": "Invalid or expired reset link."}, status=status.HTTP_400_BAD_REQUEST)

        if is_platform_account(user):
            return Response({"message": "Use the SaaS Control Panel reset link sent to your email."},
                            status=status.HTTP_400_BAD_REQUEST)

        user.set_password(serializer.validated_data["password"])
        user.email_verified = True
        user.save(update_fields=["password", "email_verified"])
        from django.utils import timezone
        MagicLink.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())

        # Verify the same account/role eligibility as a normal email/password
        # login before returning a new session. A valid reset link alone must
        # never bypass a disabled company, role, or user login restriction.
        login = LoginSerializer(
            data={"email": user.email, "password": serializer.validated_data["password"]},
            context={"request": request},
        )
        if not login.is_valid():
            return Response({
                "message": (
                    "Password reset successfully, but this account cannot sign in. "
                    "Contact your workshop administrator."
                ),
            })

        tokens = issue_tokens(login.validated_data["user"])
        tokens["message"] = "Password reset successfully. Signed in securely."
        return Response(tokens)

class MagicLinkRequestView(APIView):
    # Public auth routes must ignore stale Authorization headers from clients.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [RecoveryRateThrottle]

    def post(self, request):
        serializer = MagicLinkRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email__iexact=serializer.validated_data["email"], is_active=True).first()
        # Public recovery responses must not reveal whether an account exists.
        if user and user_can_sign_in(user):
            raw_token = MagicLink.issue(user)
            url = magic_action_url(request, user, raw_token)
            queue_email(
                subject="Your CubeGears magic login link",
                message="Use the button below to securely sign in without entering a password. This single-use link expires in 15 minutes.",
                recipient_list=[user.email],
                action_url=url, action_label="Sign in to CubeGears",
            )

        return Response({"message": "If the account exists, a magic link has been sent."})

class MagicLinkVerifyView(APIView):
    # Public auth routes must ignore stale Authorization headers from clients.
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = MagicLinkVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = MagicLink.consume(serializer.validated_data["token"])
        if not user:
            return Response({"message": "Invalid or expired magic link."}, status=status.HTTP_400_BAD_REQUEST)
        if not user_can_sign_in(user):
            return Response({
                "message": "This account cannot sign in. Contact your workshop administrator."
            }, status=status.HTTP_400_BAD_REQUEST)
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        return Response(issue_tokens(user))

class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not request.user.check_password(serializer.validated_data["current_password"]):
            return Response({"message": "Current password is incorrect."}, status=status.HTTP_400_BAD_REQUEST)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password"])
        from django.utils import timezone
        MagicLink.objects.filter(user=request.user, used_at__isnull=True).update(used_at=timezone.now())
        return Response({"message": "Password changed successfully. Please sign in again."})


class PublicWorkshopSignupView(APIView):
    # Public auth routes must ignore stale Authorization headers from clients.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicSignupRateThrottle]

    @transaction.atomic
    def post(self, request):
        serializer = PublicWorkshopSignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # A login slug must fit one DNS label so signup never creates an
        # unrouteable default address. Existing website aliases reserve names
        # independently of login slugs.
        from apps.companies.models import CompanyWebsiteSubdomain
        from apps.companies.subdomains import RESERVED_LABELS, ensure_default_subdomain
        base_slug = (slugify(data["workshop_name"])[:53].strip("-") or "workshop")
        if len(base_slug) < 3 or base_slug in RESERVED_LABELS:
            base_slug = "workshop-" + base_slug
        slug = base_slug
        suffix = 2
        while (Company.objects.filter(slug=slug).exists()
               or CompanyWebsiteSubdomain.objects.filter(label=slug).exists()):
            addition = "-" + str(suffix)
            slug = base_slug[:63 - len(addition)].rstrip("-") + addition
            suffix += 1

        company = Company.objects.create(
            name=data["workshop_name"].strip(),
            legal_name=data["workshop_name"].strip(),
            slug=slug,
            email=data.get("email") or "",
            phone=data["mobile"],
            currency="INR",
            plan="credits",
            require_paid_features=True,
            is_active=True,
        )

        # Backend owns the default website reservation. No new Vercel
        # project/domain is provisioned for each workshop signup.
        ensure_default_subdomain(company)

        branch = Branch.objects.create(
            company=company,
            name="Head Office",
            code="HO",
            phone=data["mobile"],
            email=data.get("email") or "",
            is_head_office=True,
            is_active=True,
        )

        role = Role.objects.create(
            company=company,
            name="Super Admin",
            code="SUPER_ADMIN",
            permissions=["*"],
            is_system=True,
            is_active=True,
        )


        user = User.objects.create_user(
            email=data.get("email") or None,
            password=data.get("password"),
            name=data["owner_name"].strip(),
            phone=data["mobile"].strip(),
            company=company,
            branch=branch,
            role=role,
            is_active=True,
            is_staff=False,
            is_superuser=False,
            email_verified=False,
        )
        if not data.get("password"):
            user.set_unusable_password()
            user.save(update_fields=["password"])

        company.owner = user
        company.save(update_fields=["owner", "updated_at"])
        # Initialize an empty, company-scoped billing wallet. This does NOT
        # grant credits, create a ledger debit or activate any paid feature.
        # Its real 0.00 balance is visible during mandatory onboarding.
        from apps.credits.models import CreditWallet
        CreditWallet.objects.get_or_create(company=company)

        # Newly registered owners resume setup after signing in; existing
        # workshops without this record retain their current workspace.
        from apps.saas.models import CompanySetting
        CompanySetting.objects.create(
            company=company, branch=branch, category="onboarding",
            data={"required": True, "completed": False, "completedSteps": []},
        )

        # Create the staff identity without granting a free attendance entitlement.
        employee = resolve_employee_for_user(user)
        if data.get('whatsapp_consent') and employee:
            employee.whatsapp_consent = True
            employee.whatsapp_consent_source = 'Explicit consent during workshop registration'
            employee.save(update_fields=['whatsapp_consent', 'whatsapp_consent_source', 'updated_at'])

        if data.get("password"):
            # Never create or expose a bearer setup link after a strong owner
            # password is established. The owner signs in normally with mobile.
            delivery = "password_created"
            message = "Workshop created. Sign in with your mobile number and password."
        else:
            invitation = issue_invitation(user)
            setup_url = invitation_url(invitation)
            # Preserve existing one-use email / official platform WhatsApp
            # delivery for older clients that do not submit a password.
            if user.email:
                queue_email(
                    subject="Set up your CubeGears password",
                    message="Welcome to CubeGears. Complete your workshop account setup using the secure link.",
                    recipient_list=[user.email],
                    action_url=setup_url, action_label="Set up password",
                    # In direct SMTP mode, cap each network wait below the
                    # public website's HTTP deadline. Prefer Celery for reliable
                    # delivery; a slow SMTP provider must not hold signup 15s.
                    smtp_timeout_seconds=5,
                )
            else:
                from apps.whatsapp.staff_invites import queue_platform_owner_invitation
                transaction.on_commit(lambda: queue_platform_owner_invitation(invitation.pk))
            delivery = "email_queued" if user.email else "platform_whatsapp_pending"
            message = "Workshop created. Your secure password invitation will be sent through the configured delivery channel."

        return Response(
            {
                "message": message,
                "setupDelivery": delivery,
                "workshop": {
                    "id": str(company.id),
                    "name": company.name,
                    "slug": company.slug,
                    "plan": company.plan,
                },
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


class StaffInvitationValidationView(APIView):
    # Public auth routes must ignore stale Authorization headers from clients.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [RecoveryRateThrottle]

    def post(self, request):
        record = resolve_invitation(request.data.get("token"))
        if not record:
            return Response({"valid":False,"message":"Invalid or expired setup link."}, status=400)
        return Response({"valid":True,"purpose":record.purpose,"expiresAt":record.expires_at})


class StaffInvitationConsumeView(APIView):
    # Public auth routes must ignore stale Authorization headers from clients.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [RecoveryRateThrottle]

    def post(self, request):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as PasswordValidationError
        token = request.data.get("token")
        password = request.data.get("password")
        confirmation = request.data.get("confirmPassword")
        if not isinstance(password,str) or not password or password != confirmation:
            return Response({"password":"Passwords must match."}, status=400)
        try:
            validate_password(password)
            user = consume_invitation(token, password)
        except PasswordValidationError as exc:
            return Response({"password":list(exc.messages)}, status=400)
        if user is None:
            return Response({"message":"Invalid or expired setup link."}, status=400)
        return Response({"message":"Password saved. Sign in with your registered mobile number and password."})


class SetupPasswordView(APIView):
    # Public auth routes must ignore stale Authorization headers from clients.
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user_id = urlsafe_base64_decode(serializer.validated_data["uid"]).decode()
            user = User.objects.get(pk=user_id, is_active=True)
        except Exception:
            return Response({"message": "Invalid or expired setup link."}, status=status.HTTP_400_BAD_REQUEST)

        if not default_token_generator.check_token(user, serializer.validated_data["token"]):
            return Response({"message": "Invalid or expired setup link."}, status=status.HTTP_400_BAD_REQUEST)

        if user.has_usable_password():
            return Response({"message": "Account is already set up. Use password reset."}, status=status.HTTP_400_BAD_REQUEST)

        if is_platform_account(user):
            return Response({"message": "Use the SaaS Control Panel setup link sent to your email."},
                            status=status.HTTP_400_BAD_REQUEST)

        user.is_staff = False
        user.is_superuser = False
        user.set_password(serializer.validated_data["password"])
        user.email_verified = True
        user.save(update_fields=["password", "email_verified", "is_staff", "is_superuser"])

        if not user_can_sign_in(user):
            return Response({
                "message": (
                    "Password set successfully, but this account cannot sign in. "
                    "Contact your workshop administrator."
                ),
            })

        tokens = issue_tokens(user)
        tokens["message"] = "Password set successfully."
        return Response(tokens)
