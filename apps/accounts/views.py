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
    rate = "10/minute"


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
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        return Response(issue_tokens(serializer.validated_data["user"]))

class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get("refresh")
        if refresh_token:
            try:
                RefreshToken(refresh_token).blacklist()
            except Exception:
                pass
        return Response({"message": "Logged out successfully."})

class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response(UserSerializer(user).data)

class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [RecoveryRateThrottle]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        # Identical success for unknown accounts, disabled staff, missing
        # consent, unconfigured workshops and successfully queued requests.
        generic = {"message": "If an eligible account exists, password reset instructions will be sent."}
        if data.get("mobile"):
            import hashlib
            from datetime import timedelta
            from django.core.cache import cache
            from django.utils import timezone
            from apps.accounts.models import StaffPasswordInvitation
            from apps.employees.models import Employee
            from apps.whatsapp.staff_invites import (
                staff_auto_delivery_available, queue_staff_whatsapp_invitation,
            )

            workshop = data["workshop"].strip().lower()
            mobile = data["mobile"]
            fingerprint = hashlib.sha256((workshop + ":" + mobile).encode()).hexdigest()
            # Shared cache and anonymous-IP throttle both apply; account lookup
            # outcome must never change the public response or rate-limit key.
            if not cache.add("staff-recovery:" + fingerprint, 1, timeout=600):
                return Response(generic)
            user = User.objects.filter(
                company__slug=workshop, normalized_phone=mobile,
            ).select_related("company", "role").first()
            if user and user.has_usable_password() and user_can_sign_in(user):
                employee = Employee.objects.filter(
                    company=user.company, user=user,
                    status__in=["Active", "Probation", "Notice Period"],
                ).select_related("user", "user__company", "user__role").first()
                if employee and staff_auto_delivery_available(employee):
                    with transaction.atomic():
                        # Prevent two requests invalidating one another's links.
                        locked = User.objects.select_for_update().get(pk=user.pk)
                        since = timezone.now() - timedelta(minutes=10)
                        recent = StaffPasswordInvitation.objects.filter(
                            user=locked, used_at__isnull=True, invalidated_at__isnull=True,
                            expires_at__gt=timezone.now(), created_at__gte=since,
                            delivery_status__in=["queued", "sending", "sent", "delivered", "read"],
                        ).exists()
                        if not recent:
                            invitation = issue_invitation(locked, purpose="reset")
                            queue_staff_whatsapp_invitation(invitation, employee)
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
    permission_classes = [AllowAny]
    throttle_classes = [PublicSignupRateThrottle]

    @transaction.atomic
    def post(self, request):
        serializer = PublicWorkshopSignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        base_slug = slugify(data["workshop_name"]) or "workshop"
        slug = base_slug
        suffix = 2
        while Company.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{suffix}"
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
            password=None,
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
        user.set_unusable_password()
        user.save(update_fields=["password"])

        company.owner = user
        company.save(update_fields=["owner", "updated_at"])

        # Create the staff identity without granting a free attendance entitlement.
        employee = resolve_employee_for_user(user)
        if data.get('whatsapp_consent') and employee:
            employee.whatsapp_consent = True
            employee.whatsapp_consent_source = 'Explicit consent during workshop registration'
            employee.save(update_fields=['whatsapp_consent', 'whatsapp_consent_source', 'updated_at'])

        invitation = issue_invitation(user)
        setup_url = invitation_url(invitation)
        # Email remains supported. Mobile-only registration needs an official,
        # independently configured platform WhatsApp/SMS sender to deliver the
        # invite: do not leak a bearer link back to an anonymous signup caller.
        if user.email:
            queue_email(
                subject="Set up your CubeGears password",
                message="Welcome to CubeGears. Complete your workshop account setup using the secure link.",
                recipient_list=[user.email],
                action_url=setup_url, action_label="Set up password",
            )
        else:
            from apps.whatsapp.staff_invites import queue_platform_owner_invitation
            transaction.on_commit(lambda: queue_platform_owner_invitation(invitation.pk))

        return Response(
            {
                "message": "Workshop account created. Your password setup invitation will be delivered to the registered contact when a delivery channel is configured.",
                "setupDelivery": "email_queued" if user.email else "platform_whatsapp_pending",
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
    permission_classes = [AllowAny]
    throttle_classes = [RecoveryRateThrottle]

    def post(self, request):
        record = resolve_invitation(request.data.get("token"))
        if not record:
            return Response({"valid":False,"message":"Invalid or expired setup link."}, status=400)
        return Response({"valid":True,"purpose":record.purpose,"expiresAt":record.expires_at})


class StaffInvitationConsumeView(APIView):
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
