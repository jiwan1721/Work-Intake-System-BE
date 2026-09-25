"""User management views: forgot/reset password, change password."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers as drf_serializers
from rest_framework import status
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
from users.constants import OTP_FROM_EMAIL, OTP_PASSWORD_RESET_SUBJECT
from users.otp import PURPOSE_PASSWORD_RESET, generate_otp, verify_otp

User = get_user_model()


@extend_schema(tags=["auth"])
class ForgotPasswordView(APIView):
    """POST /api/v1/auth/forgot-password/

    Sends a 6-digit OTP to the email address. Always returns 200 to prevent
    user enumeration.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        summary="Request password reset",
        request=ForgotPasswordSerializer,
        responses={200: OpenApiResponse(description="OTP sent if account exists.")},
    )
    def post(self, request: Request) -> Response:
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"].lower()
        user = User.objects.filter(email=email, is_active=True).first()

        if user:
            code = generate_otp(user.email, PURPOSE_PASSWORD_RESET)
            send_mail(
                subject=OTP_PASSWORD_RESET_SUBJECT,
                message=(
                    f"Hi {user.first_name},\n\n"
                    f"Your password reset code is: {code}\n\n"
                    f"It expires in 10 minutes.\n"
                    "If you did not request this, ignore this email."
                ),
                from_email=OTP_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )

        return Response(
            {"message": "If an account with that email exists, a reset code has been sent."}
        )


@extend_schema(tags=["auth"])
class ResetPasswordView(APIView):
    """POST /api/v1/auth/reset-password/

    Validates the OTP from forgot-password and sets a new password.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        summary="Reset password",
        request=ResetPasswordSerializer,
        responses={
            200: OpenApiResponse(description="Password reset successfully."),
            400: OpenApiResponse(description="Invalid or expired OTP."),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        code = serializer.validated_data["otp"]
        new_password = serializer.validated_data["password"]

        user = User.objects.filter(email=email, is_active=True).first()
        if user is None or not verify_otp(email, code, PURPOSE_PASSWORD_RESET):
            raise drf_serializers.ValidationError({"otp": "Invalid or expired reset code."})

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
