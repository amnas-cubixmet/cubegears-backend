"""JWT access and refresh enforcement for real workshop membership.

Authentication checks live account/tenant/role status on *every* API request,
not only when the original email/mobile password was accepted. This closes the
gap where a disabled workshop role could continue using an unexpired access or
refresh token on an endpoint that only required IsAuthenticated.
"""

from django.contrib.auth import get_user_model
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from .serializers import user_can_sign_in


class CompanyJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if not user_can_sign_in(user):
            raise AuthenticationFailed(
                "This account no longer has access. Contact your workshop administrator.",
                code="account_inactive",
            )
        return user


class CompanyTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        # Verify account state before issuing new credentials/rotating a refresh
        # token. Do not trust tenant or role claims embedded in older JWTs.
        token = RefreshToken(attrs["refresh"])
        user_id = token.get(api_settings.USER_ID_CLAIM)
        if not user_id:
            raise AuthenticationFailed("Your session is no longer valid.", code="account_inactive")
        user = get_user_model().objects.select_related(
            "company", "role",
        ).filter(**{api_settings.USER_ID_FIELD: user_id}).first()
        if not user_can_sign_in(user):
            raise AuthenticationFailed(
                "This account no longer has access. Contact your workshop administrator.",
                code="account_inactive",
            )
        return super().validate(attrs)


class CompanyTokenRefreshView(TokenRefreshView):
    serializer_class = CompanyTokenRefreshSerializer
