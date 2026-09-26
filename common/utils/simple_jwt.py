"""JWT token parsing utility (adapted from BaseProject core/common/utils/simple_jwt.py)."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.backends import TokenBackend
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.utils import datetime_from_epoch

jwt_config = settings.SIMPLE_JWT

simple_jwt_backend = TokenBackend(
    jwt_config.get("ALGORITHM"),
    jwt_config.get("SIGNING_KEY"),
    jwt_config.get("VERIFYING_KEY"),
)

User = get_user_model()


class SimpleJWTParser:
    def __init__(self, token: bytes | str) -> None:
        self.token = token

    @property
    def parsed(self) -> dict | None:
        if hasattr(self, "_parsed_token"):
            return self._parsed_token
        try:
            self._parsed_token = simple_jwt_backend.decode(token=self.token)
            return self._parsed_token
        except TokenError:
            return None

    @property
    def token_created(self):
        """Return the datetime when this token was issued."""
        parsed = self.parsed
        if parsed is None:
            return None
        exp = parsed.get("exp")
        token_type = parsed.get("token_type")
        lifetime_key = {
            "access": "ACCESS_TOKEN_LIFETIME",
            "refresh": "REFRESH_TOKEN_LIFETIME",
        }.get(token_type)
        if exp is None or lifetime_key is None:
            return None
        lifetime = jwt_config.get(lifetime_key)
        return datetime_from_epoch(exp) - lifetime

    @property
    def user(self):
        parsed = self.parsed
        if parsed is None:
            return None
        return User.objects.get(id=parsed.get("user_id"))
