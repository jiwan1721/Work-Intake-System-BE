"""Custom JWT authentication (adapted from BaseProject core/common/utils/authentication.py).

Extends DRF Simple JWT's standard JWTAuthentication with three extra checks:
- User must be active (is_active=True)
- User must not be blocked (is_blocked=False)
- Token must have been issued after the user's last password change so that
  changing a password instantly invalidates all outstanding tokens.
"""

from __future__ import annotations

from datetime import timezone as tz

from django.conf import settings
from django.utils.translation import gettext as _
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.utils import datetime_from_epoch

# In production, hide the specific reason from the client to avoid leaking
# information about why authentication failed.
_DEBUG_ERRORS = getattr(settings, "DEBUG", False)


def _fail(msg: str) -> None:
    raise AuthenticationFailed(_(msg if _DEBUG_ERRORS else "Unable to authenticate."))


class CustomJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, token = result

        if not user.is_active:
            _fail("User account is inactive.")
        if getattr(user, "is_blocked", False):
            _fail("User account is blocked.")

        # Reject tokens issued before the user's last password change so
        # a stolen token stops working as soon as the owner resets their password.
        token_refresh_date = getattr(user, "token_refresh_date", None)
        if token_refresh_date is not None:
            jwt_config = settings.SIMPLE_JWT
            exp = token.get("exp")
            token_type = token.get("token_type")
            lifetime_key = {
                "access": "ACCESS_TOKEN_LIFETIME",
                "refresh": "REFRESH_TOKEN_LIFETIME",
            }.get(token_type)
            if exp is not None and lifetime_key is not None:
                lifetime = jwt_config.get(lifetime_key)
                issued_at = datetime_from_epoch(exp) - lifetime
                if token_refresh_date.tzinfo is None:
                    token_refresh_date = token_refresh_date.replace(tzinfo=tz.utc)
                if issued_at < token_refresh_date:
                    _fail("Token has been invalidated. Please log in again.")

        return user, token
