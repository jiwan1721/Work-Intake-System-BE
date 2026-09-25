"""Auth views: login, register, token refresh, current user.

Adapted from BaseProject core/users/api/v1/views/auth.py.
"""

from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema
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
)
from users.api.v1.serializers.user import UserSerializer


@extend_schema(tags=["auth"])
class LoginView(APIView):
    """POST /api/v1/auth/login/ — exchange credentials for JWT tokens."""

    permission_classes = [AllowAny]

    @extend_schema(
        summary="Login",
        request=LoginSerializer,
        responses={
            200: OpenApiResponse(description="Returns access token, refresh token, and user."),
            401: OpenApiResponse(description="Invalid credentials."),
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
    """POST /api/v1/auth/register/ — create a new operator account."""

    permission_classes = [AllowAny]

    @extend_schema(
        summary="Register",
        request=RegisterSerializer,
        responses={
            201: OpenApiResponse(description="Returns access token, refresh token, and user."),
            400: OpenApiResponse(description="Validation error."),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
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
