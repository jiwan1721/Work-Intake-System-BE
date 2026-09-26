"""Auth views: login, register, OTP verify, token refresh, current user."""

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
from rest_framework_simplejwt.tokens import RefreshToken

from users.api.v1.serializers.auth import (
    CustomTokenRefreshSerializer,
    LoginSerializer,
    RegisterSerializer,
    ResendOTPSerializer,
    VerifyEmailSerializer,
)
from users.api.v1.serializers.user import UserSerializer
from users.constants import OTP_FROM_EMAIL, OTP_VERIFY_EMAIL_SUBJECT
from users.otp import PURPOSE_REGISTRATION, generate_otp, verify_otp

User = get_user_model()


@extend_schema(tags=["auth"])
class LoginView(APIView):
    """POST /api/v1/auth/login/ — exchange credentials for JWT tokens."""

    permission_classes = [AllowAny]

    @extend_schema(
        summary="Login",
        request=LoginSerializer,
        responses={
            200: OpenApiResponse(description="Returns access token, refresh token, and user."),
            401: OpenApiResponse(description="Invalid credentials or unverified email."),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return Response(
            {
                "access": data["access"],
                "refresh": data["refresh"],
                "user": UserSerializer(data["user"]).data,
            }
        )


@extend_schema(tags=["auth"])
class RegisterView(APIView):
    """POST /api/v1/auth/register/ — create account and send email verification OTP."""

    permission_classes = [AllowAny]

    @extend_schema(
        summary="Register",
        request=RegisterSerializer,
        responses={
            201: OpenApiResponse(description="Account created. OTP sent to email."),
            400: OpenApiResponse(description="Validation error."),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        code = generate_otp(user.email, PURPOSE_REGISTRATION)
        send_mail(
            subject=OTP_VERIFY_EMAIL_SUBJECT,
            message=(
                f"Hi {user.first_name},\n\n"
                f"Your verification code is: {code}\n\n"
                f"It expires in 10 minutes. If you did not register, ignore this email."
            ),
            from_email=OTP_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=True,
        )

        return Response(
            {
                "message": "Account created. Please check your email for a verification code.",
                "email": user.email,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["auth"])
class VerifyEmailView(APIView):
    """POST /api/v1/auth/verify-email/ — verify OTP and activate account."""

    permission_classes = [AllowAny]

    @extend_schema(
        summary="Verify email",
        request=VerifyEmailSerializer,
        responses={
            200: OpenApiResponse(description="Email verified. Returns tokens and user."),
            400: OpenApiResponse(description="Invalid or expired OTP."),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        code = serializer.validated_data["otp"]

        user = User.objects.filter(email=email, is_active=False).first()
        if user is None or not verify_otp(email, code, PURPOSE_REGISTRATION):
            raise drf_serializers.ValidationError({"otp": "Invalid or expired verification code."})

        user.is_active = True
        user.save(update_fields=["is_active"])

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserSerializer(user).data,
            }
        )


@extend_schema(tags=["auth"])
class ResendOTPView(APIView):
    """POST /api/v1/auth/resend-otp/ — resend the email verification OTP."""

    permission_classes = [AllowAny]

    @extend_schema(
        summary="Resend email verification OTP",
        request=ResendOTPSerializer,
        responses={
            200: OpenApiResponse(description="OTP resent if account exists and is unverified."),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = ResendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        user = User.objects.filter(email=email, is_active=False, is_blocked=False).first()
        if user:
            code = generate_otp(user.email, PURPOSE_REGISTRATION)
            send_mail(
                subject=OTP_VERIFY_EMAIL_SUBJECT,
                message=(
                    f"Hi {user.first_name},\n\n"
                    f"Your new verification code is: {code}\n\n"
                    f"It expires in 10 minutes."
                ),
                from_email=OTP_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )

        return Response(
            {"message": "If an unverified account with that email exists, a new code has been sent."}
        )


@extend_schema(tags=["auth"])
class TokenRefreshView(APIView):
    """POST /api/v1/auth/refresh/ — exchange a refresh token for a new access token."""

    permission_classes = [AllowAny]

    @extend_schema(
        summary="Refresh token",
        responses={
            200: OpenApiResponse(description="Returns a new access token."),
            400: OpenApiResponse(description="Invalid or expired refresh token."),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = CustomTokenRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data)


@extend_schema(tags=["auth"])
class MeView(APIView):
    """GET /api/v1/auth/me/ — return the authenticated user's profile."""

    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Get current user")
    def get(self, request: Request) -> Response:
        return Response(UserSerializer(request.user).data)
