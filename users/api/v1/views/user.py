"""User management views: forgot/reset password, change password.

Adapted from BaseProject core/users/api/v1/views/user.py.
Uses Django's built-in PasswordResetTokenGenerator (no token storage in DB).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.mail import send_mail
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from users.api.v1.serializers.user import (
    ForgotPasswordSerializer,
    PasswordChangeSerializer,
    ResetPasswordSerializer,
    UserSerializer,
)
from users.constants import PASSWORD_RESET_FROM_EMAIL, PASSWORD_RESET_SUBJECT

User = get_user_model()
_token_generator = PasswordResetTokenGenerator()


@extend_schema(tags=["auth"])
class ForgotPasswordView(APIView):
    """POST /api/v1/auth/forgot-password/

    Sends a password reset email. Always returns 200 regardless of whether
    the email exists to prevent user enumeration.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        summary="Request password reset",
        request=ForgotPasswordSerializer,
        responses={200: OpenApiResponse(description="Reset email sent if account exists.")},
    )
    def post(self, request: Request) -> Response:
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"].lower()
        user = User.objects.filter(email=email, is_active=True).first()

        if user:
            from django.conf import settings

            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = _token_generator.make_token(user)
            frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:5173")
            reset_url = f"{frontend_url}/reset-password/{uid}/{token}/"

            send_mail(
                subject=PASSWORD_RESET_SUBJECT,
                message=(
                    f"Hi {user.first_name},\n\n"
                    f"Click the link below to reset your password:\n{reset_url}\n\n"
                    "This link expires after 24 hours.\n"
                    "If you did not request this, ignore this email."
                ),
                from_email=PASSWORD_RESET_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )

        return Response(
            {"message": "If an account with that email exists, a reset link has been sent."}
        )


@extend_schema(tags=["auth"])
class ResetPasswordView(APIView):
    """POST /api/v1/auth/reset-password/

    Validates the uidb64 + token from the reset email and sets a new password.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        summary="Reset password",
        request=ResetPasswordSerializer,
        responses={
            200: OpenApiResponse(description="Password reset successfully."),
            400: OpenApiResponse(description="Invalid or expired reset link."),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uid = serializer.validated_data["uid"]
        token = serializer.validated_data["token"]
        new_password = serializer.validated_data["password"]

        try:
            user_pk = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=user_pk)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            raise serializers.ValidationError({"uid": "Invalid reset link."})

        if not _token_generator.check_token(user, token):
            raise serializers.ValidationError({"token": "Reset link is invalid or has expired."})

        user.set_password(new_password)
        user.save(update_fields=["password", "token_refresh_date"])

        return Response({"message": "Password has been reset successfully."})


@extend_schema(tags=["auth"])
class ChangePasswordView(APIView):
    """POST /api/v1/auth/change-password/ — change password while logged in."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Change password",
        responses={
            200: OpenApiResponse(description="Password changed. Re-login required."),
            400: OpenApiResponse(description="Validation error."),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {"message": "Password changed successfully. Please log in again with your new password."}
        )
