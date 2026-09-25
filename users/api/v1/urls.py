from django.urls import path

from users.api.v1.views.auth import LoginView, MeView, RegisterView, TokenRefreshView
from users.api.v1.views.user import ChangePasswordView, ForgotPasswordView, ResetPasswordView

urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    path("register/", RegisterView.as_view(), name="register"),
    path("refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("me/", MeView.as_view(), name="me"),
    path("forgot-password/", ForgotPasswordView.as_view(), name="forgot-password"),
    path("reset-password/", ResetPasswordView.as_view(), name="reset-password"),
    path("change-password/", ChangePasswordView.as_view(), name="change-password"),
]
