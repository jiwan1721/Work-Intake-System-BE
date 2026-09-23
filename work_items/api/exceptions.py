"""One error shape for the whole API (PLAN §8).

    {"error": {"code": "...", "message": "...", "details": {...}}}

A client should never have to guess which of three formats it got. Domain
errors carry their own code and status, so adding a new failure mode means
adding a class in `domain/errors.py`, not a branch in a view.

This handler is shared by every API version; only serializers and URLs are
versioned (PLAN §8.1).
"""

from __future__ import annotations

import logging
from typing import Any

from django.http import Http404, JsonResponse
from django.views.defaults import page_not_found
from rest_framework import status as http_status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from ..domain.errors import DomainError

logger = logging.getLogger(__name__)

CODE_VALIDATION_ERROR = "VALIDATION_ERROR"
CODE_NOT_FOUND = "NOT_FOUND"
CODE_UNAUTHORIZED = "UNAUTHORIZED"
CODE_METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
CODE_INTERNAL_ERROR = "INTERNAL_ERROR"

#: Fallback code per HTTP status, for DRF exceptions we do not name explicitly.
_STATUS_CODES = {
    http_status.HTTP_400_BAD_REQUEST: CODE_VALIDATION_ERROR,
    http_status.HTTP_401_UNAUTHORIZED: CODE_UNAUTHORIZED,
    http_status.HTTP_403_FORBIDDEN: "FORBIDDEN",
    http_status.HTTP_404_NOT_FOUND: CODE_NOT_FOUND,
    http_status.HTTP_405_METHOD_NOT_ALLOWED: CODE_METHOD_NOT_ALLOWED,
    http_status.HTTP_406_NOT_ACCEPTABLE: "NOT_ACCEPTABLE",
    http_status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "UNSUPPORTED_MEDIA_TYPE",
    http_status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMITED",
}


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def custom_exception_handler(exc: Exception, context: dict) -> Response:
    """Map every exception a view can raise onto the envelope."""
    if isinstance(exc, DomainError):
        return Response(
            error_body(exc.code, exc.message, exc.details),
            status=exc.http_status,
        )

    if isinstance(exc, DRFValidationError):
        return Response(
            error_body(
                CODE_VALIDATION_ERROR,
                "The request body failed validation.",
                _validation_details(exc),
            ),
            status=http_status.HTTP_400_BAD_REQUEST,
        )

    if isinstance(exc, Http404):
        return Response(
            error_body(CODE_NOT_FOUND, "The requested resource does not exist."),
            status=http_status.HTTP_404_NOT_FOUND,
        )

    response = drf_exception_handler(exc, context)
    if response is not None:
        code = _STATUS_CODES.get(response.status_code)
        if code is None:
            # An APIException we have not named: use its own code rather than
            # flattening everything to INTERNAL_ERROR.
            declared = str(getattr(exc, "default_code", "") or "")
            code = declared.upper() or CODE_INTERNAL_ERROR
        response.data = error_body(code, _message_of(exc))
        return response

    # Nothing recognised it: this is a bug. Log the stack trace for us, and
    # give the client a clean envelope with nothing internal leaked.
    logger.exception("Unhandled exception in %s", context.get("request"))
    return Response(
        error_body(CODE_INTERNAL_ERROR, "Something went wrong on our side."),
        status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _message_of(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if detail is None:
        return str(exc)
    if isinstance(detail, list | dict):
        return str(detail)
    return str(detail)


def _validation_details(exc: DRFValidationError) -> dict[str, Any]:
    """Field name -> list of messages, in the camelCase the client sent."""
    detail = exc.detail
    if isinstance(detail, dict):
        return {field: _flatten(messages) for field, messages in detail.items()}
    return {"nonFieldErrors": _flatten(detail)}


def _flatten(messages: Any) -> list[str]:
    if isinstance(messages, list | tuple):
        return [str(message) for message in messages]
    return [str(messages)]


def api_not_found(request, exception=None):
    """Project-wide 404 handler.

    Paths under /api/ get the JSON envelope so a client calling an unknown
    version sees the same shape as every other error. Everything else keeps
    Django's default page.
    """
    if request.path.startswith("/api/"):
        return JsonResponse(
            error_body(
                CODE_NOT_FOUND,
                f"No API endpoint matches {request.path}.",
                {"path": request.path},
            ),
            status=http_status.HTTP_404_NOT_FOUND,
        )
    return page_not_found(request, exception)
