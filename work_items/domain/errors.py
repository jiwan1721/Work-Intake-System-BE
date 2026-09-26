"""Domain errors.

Each carries the `code` that the API layer puts in the error envelope
(PLAN §8), so the mapping from a domain failure to an HTTP response is data,
not a chain of isinstance checks spread across views.
"""

from __future__ import annotations

from .status import WorkItemStatus


class DomainError(Exception):
    """Base class for every expected, non-bug failure in the domain."""

    code = "DOMAIN_ERROR"
    http_status = 400

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class WorkItemNotFound(DomainError):
    code = "NOT_FOUND"
    http_status = 404

    def __init__(self, item_id: object) -> None:
        super().__init__(f"Work item {item_id} does not exist.", {"id": str(item_id)})


class InvalidTransition(DomainError):
    code = "INVALID_TRANSITION"
    http_status = 409

    def __init__(self, current: WorkItemStatus | str, to_status: WorkItemStatus | str) -> None:
        self.current_status = str(current)
        self.to_status = str(to_status)
        super().__init__(
            f"Cannot move work item from {self.current_status} to {self.to_status}.",
            {"currentStatus": self.current_status, "requestedStatus": self.to_status},
        )


class DuplicateConflict(DomainError):
    """Same externalId, different content: a replay we must not silently accept."""

    code = "DUPLICATE_CONFLICT"
    http_status = 409

    def __init__(self, external_id: str) -> None:
        self.external_id = external_id
        super().__init__(
            f"Work item {external_id!r} already exists with different content. "
            "The stored item was not modified.",
            {"external_id": external_id},
        )


class NotRetryable(DomainError):
    code = "NOT_RETRYABLE"
    http_status = 409

    def __init__(self, current: WorkItemStatus | str) -> None:
        self.current_status = str(current)
        super().__init__(
            f"Only failed work items can be retried; this one is {self.current_status}.",
            {"currentStatus": self.current_status},
        )


class MaxAttemptsReached(DomainError):
    code = "MAX_ATTEMPTS_REACHED"
    http_status = 409

    def __init__(self, attempt_count: int, max_attempts: int) -> None:
        self.attempt_count = attempt_count
        self.max_attempts = max_attempts
        super().__init__(
            f"Work item has used all {max_attempts} analysis attempts. "
            "A human needs to take it from here.",
            {"attemptCount": attempt_count, "maxAttempts": max_attempts},
        )
