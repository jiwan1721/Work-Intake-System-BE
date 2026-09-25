"""User serializers (adapted from BaseProject core/users/api/v1/serializers/user.py)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from common.mixins.serializers import DynamicFieldsModelSerializer, DynamicFieldsSerializerMixin

User = get_user_model()


class UserSerializer(DynamicFieldsModelSerializer):
    fullName = serializers.CharField(source="full_name", read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "firstName",
            "lastName",
            "fullName",
            "isActive",
            "dateJoined",
        )
        # Use camelCase aliases to match the rest of the API contract.
        extra_kwargs = {
            "firstName": {"source": "first_name"},
            "lastName": {"source": "last_name"},
            "isActive": {"source": "is_active"},
            "dateJoined": {"source": "date_joined"},
        }
        read_only_fields = ("id", "email", "isActive", "dateJoined")


class PasswordChangeSerializer(DynamicFieldsSerializerMixin, serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        validate_password(attrs["new_password"], self.context["request"].user)
        return attrs

    def save(self):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password", "token_refresh_date"])
        return user


class ForgotPasswordSerializer(DynamicFieldsSerializerMixin, serializers.Serializer):
    email = serializers.EmailField()


class ResetPasswordSerializer(DynamicFieldsSerializerMixin, serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return attrs
