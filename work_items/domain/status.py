"""The work item lifecycle (PLAN §5).

Pure Python: nothing in `domain/` imports Django, so the state machine can be
unit-tested without a database and reused by any caller.
"""

from __future__ import annotations

from enum import StrEnum


class WorkItemStatus(StrEnum):
    RECEIVED = "RECEIVED"
    ANALYSING = "ANALYSING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"

    @classmethod
    def values(cls) -> list[str]:
        """Every valid status, for the database CHECK constraint."""
        return [member.value for member in cls]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        """Django model field choices."""
        return [(member.value, member.value.replace("_", " ").title()) for member in cls]


#: Statuses an item can never leave.
TERMINAL_STATUSES = frozenset({WorkItemStatus.COMPLETED})


class TransitionActor(StrEnum):
    """Who caused a status change (PLAN §4, audit trail)."""

    SYSTEM = "system"
    OPERATOR = "operator"

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(member.value, member.value.title()) for member in cls]


class AttemptOutcome(StrEnum):
    """How a single LLM call ended (PLAN §4, `AnalysisAttempt`)."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(member.value, member.value.title()) for member in cls]
