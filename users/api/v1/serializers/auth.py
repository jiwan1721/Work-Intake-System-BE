"""Auth serializers (adapted from BaseProject core/users/api/v1/serializers/auth.py)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()

_LOGIN_FAILED = "Unable to log in with the provided credentials."


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        email = attrs["email"].strip().lower()
        password = attrs["password"]

        user = User.objects.filter(email=email).first()
        if user is None or not user.check_password(password):
            raise AuthenticationFailed(_(_LOGIN_FAILED))
        if not user.is_active:
            raise AuthenticationFailed(_("User account is inactive."))
        if user.is_blocked:
            raise AuthenticationFailed(_("User account is blocked."))

        refresh = RefreshToken.for_user(user)
        return {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": user,
        }


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "password", "confirm_password")

    def validate_email(self, value):
        if User.objects.filter(email=value.lower()).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value.lower()

    def validate(self, attrs):
        if attrs["password"] != attrs.pop("confirm_password"):
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            first_name=validated_data.get("first_name", ""),
            last_name=validated_data.get("last_name", ""),
        )


class CustomTokenRefreshSerializer(TokenRefreshSerializer):
    """Extend the default refresh to reject inactive or blocked users."""

    def validate(self, attrs):
        data = super().validate(attrs)

        # Decode the incoming refresh token to get the user_id for validation.
        from rest_framework_simplejwt.tokens import RefreshToken as RT
        refresh = RT(attrs["refresh"])
        user_id = refresh.get("user_id")
        if user_id:
            user = User.objects.filter(id=user_id).first()
            if user:
                if not user.is_active:
                    raise AuthenticationFailed(_("User account is inactive."))
                if getattr(user, "is_blocked", False):
                    raise AuthenticationFailed(_("User account is blocked."))
        return data
