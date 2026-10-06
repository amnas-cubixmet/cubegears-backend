from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.text import slugify
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import MagicLink
from .tasks import send_email_task
from .serializers import (
    ChangePasswordSerializer,
    ForgotPasswordSerializer,
    LoginSerializer,
    MagicLinkRequestSerializer,
    MagicLinkVerifySerializer,
    ResetPasswordSerializer,
    PublicWorkshopSignupSerializer,
    UserSerializer,
)

from apps.branches.models import Branch
from apps.companies.models import Company
from apps.roles.models import Role

User = get_user_model()


def queue_email(subject, message, recipient_list):
    transaction.on_commit(
        lambda: send_email_task.delay(
            subject=subject,
            message=message,
            recipient_list=recipient_list,
            from_email=settings.DEFAULT_FROM_EMAIL,
        ),
        robust=True,
    )


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
        return Response(UserSerializer(request.user).data)

class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email__iexact=serializer.validated_data["email"], is_active=True).first()

        if user:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_url = f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}"
            queue_email(
                subject="Reset your CubixGear password",
                message=f"Open this link to reset your password: {reset_url}",
                recipient_list=[user.email],
            )

        return Response({"message": "If the account exists, a reset link has been sent."})

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

        user.set_password(serializer.validated_data["password"])
        user.save(update_fields=["password"])
        return Response({"message": "Password reset successfully."})

class MagicLinkRequestView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = MagicLinkRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email__iexact=serializer.validated_data["email"], is_active=True).first()

        if user:
            raw_token = MagicLink.issue(user)
            url = f"{settings.FRONTEND_URL}/magic-link/verify?token={raw_token}"
            queue_email(
                subject="Your CubixGear magic login link",
                message=f"Open this secure link to sign in: {url}",
                recipient_list=[user.email],
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
        return Response({"message": "Password changed successfully."})


class PublicWorkshopSignupView(APIView):
    permission_classes = [AllowAny]

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
            email=data["email"],
            phone=data["mobile"].strip(),
            city=data["city"].strip(),
            state=data["state"].strip(),
            country=data["country"].strip(),
            currency="INR",
            plan="starter",
            is_active=True,
        )

        branch = Branch.objects.create(
            company=company,
            name="Head Office",
            code="HO",
            phone=data["mobile"].strip(),
            email=data["email"],
            city=data["city"].strip(),
            state=data["state"].strip(),
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
            email=data["email"],
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

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        setup_url = f"{settings.FRONTEND_URL}/setup-password?uid={uid}&token={token}"
        queue_email(
            subject="Set up your CubixGear password",
            message=(
                f"Welcome to CubixGear. Your workshop account is ready.\n\n"
                f"Set your password using this secure link:\n{setup_url}\n\n"
                f"If you did not create this account, you can ignore this email."
            ),
            recipient_list=[user.email],
        )

        return Response(
            {
                "message": "Workshop account created. Check your email to set your password.",
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

        # Upgrade owners created by the earlier public-signup flow.
        # This remains a tenant-level super admin role only; it does not grant
        # Django/platform staff or global CubixGear control-panel access.
        role = getattr(user, "role", None)
        if (
            role
            and role.company_id == user.company_id
            and role.code == "ADMIN"
            and "*" in (role.permissions or [])
        ):
            role.name = "Super Admin"
            role.code = "SUPER_ADMIN"
            role.is_system = True
            role.is_active = True
            role.save(update_fields=["name", "code", "is_system", "is_active"])

        user.is_staff = False
        user.is_superuser = False
        user.set_password(serializer.validated_data["password"])
        user.email_verified = True
        user.save(update_fields=["password", "email_verified", "is_staff", "is_superuser"])

        tokens = issue_tokens(user)
        tokens["message"] = "Password set successfully."
        return Response(tokens)
