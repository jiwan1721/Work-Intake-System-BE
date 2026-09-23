"""Shared-secret auth for the intake endpoint (BE-14, PLAN §13).

The external system posts work items machine-to-machine, so it gets an API
key; operator endpoints are out of scope for this assessment and stay open.

Two details that matter:

- the comparison is constant-time, so a wrong key cannot be discovered a byte
  at a time by timing the responses;
- an empty `INTAKE_API_KEY` disables the check entirely, which keeps the
  default local demo a single `docker compose up` with nothing to configure.
"""

from __future__ import annotations

import hmac

from django.conf import settings
from rest_framework.exceptions import APIException
from rest_framework.permissions import BasePermission

API_KEY_HEADER = "X-API-Key"


class ApiKeyRequired(APIException):
    """401, not 403: the caller has not identified itself at all.

    DRF's own `AuthenticationFailed` is downgraded to 403 when a view has no
    authentication classes, which is exactly our situation — so the status is
    set explicitly here instead.
    """

    status_code = 401
    default_detail = f"A valid {API_KEY_HEADER} header is required."
    default_code = "unauthorized"


class HasIntakeApiKey(BasePermission):
    """Require `X-API-Key` when `INTAKE_API_KEY` is configured."""

    def has_permission(self, request, view) -> bool:
        expected = settings.INTAKE_API_KEY
        if not expected:
            return True

        provided = request.headers.get(API_KEY_HEADER, "")
        if not hmac.compare_digest(provided, expected):
            raise ApiKeyRequired
        return True
