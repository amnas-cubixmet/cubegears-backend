"""Enforce the current workshop/role status for every JWT-authenticated request."""

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import AuthenticationFailed


class CompanyJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        # Delayed import avoids loading DRF serializers while its settings and
        # APIView modules are still initializing.
        from .serializers import user_can_sign_in

        user = super().get_user(validated_token)
        if not user_can_sign_in(user):
            raise AuthenticationFailed(
                "This account no longer has access. Contact your workshop administrator.",
                code="account_inactive",
            )
        return user
