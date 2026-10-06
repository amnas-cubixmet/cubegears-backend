from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    ChangePasswordView,
    ForgotPasswordView,
    LoginView,
    LogoutView,
    MagicLinkRequestView,
    MagicLinkVerifyView,
    MeView,
    ResetPasswordView,
    PublicWorkshopSignupView,
    SetupPasswordView,
)

urlpatterns = [
    path("signup", PublicWorkshopSignupView.as_view(), name="public-signup"),
    path("setup-password", SetupPasswordView.as_view(), name="setup-password"),
    path("login", LoginView.as_view(), name="login"),
    path("logout", LogoutView.as_view(), name="logout"),
    path("me", MeView.as_view(), name="me"),
    path("refresh", TokenRefreshView.as_view(), name="token-refresh"),
    path("change-password", ChangePasswordView.as_view(), name="change-password"),
    path("forgot-password", ForgotPasswordView.as_view(), name="forgot-password"),
    path("reset-password", ResetPasswordView.as_view(), name="reset-password"),
    path("magic-link", MagicLinkRequestView.as_view(), name="magic-link-request"),
    path("magic-link/verify", MagicLinkVerifyView.as_view(), name="magic-link-verify"),
]
