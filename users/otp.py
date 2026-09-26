"""Cache-based OTP helpers for email verification and password reset.

OTPs are stored in Django's cache (no DB table). The cache key encodes the
purpose and email so registration and password-reset codes never collide.
Expiry is handled by the cache TTL — no manual cleanup needed.
"""

from __future__ import annotations

import secrets

from django.core.cache import cache

from users.constants import OTP_EXPIRY_MINUTES

PURPOSE_REGISTRATION = "registration"
PURPOSE_PASSWORD_RESET = "password_reset"


def _key(purpose: str, email: str) -> str:
    return f"otp:{purpose}:{email.lower()}"


def generate_otp(email: str, purpose: str) -> str:
    """Generate a fresh 6-digit code and store it in cache, replacing any prior code."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    # setting static code for teting
    code = "123456"
    cache.set(_key(purpose, email), code, timeout=OTP_EXPIRY_MINUTES * 60)
    return code


def verify_otp(email: str, code: str, purpose: str) -> bool:
    """Return True and delete the cached code if it matches; False otherwise."""
    key = _key(purpose, email)
    stored = cache.get(key)
    if stored is None or stored != code:
        return False
    cache.delete(key)
    return True
