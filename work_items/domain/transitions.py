"""The transition table and the actions it permits (PLAN §5).

This module is the single definition of "what may happen next". The services
enforce it before touching the database, the database re-checks it with a
conditional UPDATE, and the API serialises `allowed_actions()` so the frontend
renders buttons from the same rules instead of reimplementing them.
"""

from __future__ import annotations

from .errors import InvalidTransition
from .status import WorkItemStatus

S = WorkItemStatus

#: from-status → the set of statuses it may move to.
TRANSITIONS: dict[WorkItemStatus, frozenset[WorkItemStatus]] = {
    S.RECEIVED: frozenset({S.ANALYSING}),
    S.ANALYSING: frozenset({S.READY_FOR_REVIEW, S.FAILED}),
    S.FAILED: frozenset({S.ANALYSING}),
    S.READY_FOR_REVIEW: frozenset({S.COMPLETED}),
    S.COMPLETED: frozenset(),
}

#: The only transitions an operator may request directly through PATCH /status.
#: Everything else is system-only: reaching ANALYSING or READY_FOR_REVIEW
#: requires actually running an analysis, so those states cannot be faked by
#: PATCHing a status (PLAN §5, "System vs. manual transitions").
MANUAL_TRANSITIONS: frozenset[tuple[WorkItemStatus, WorkItemStatus]] = frozenset(
    {(S.READY_FOR_REVIEW, S.COMPLETED)}
)

# Action names exposed to the client in `allowedActions`.
ACTION_ANALYSE = "analyse"
ACTION_RETRY = "retry"
ACTION_COMPLETE = "complete"


def can_transition(from_: WorkItemStatus, to: WorkItemStatus) -> bool:
    """True if `from_ → to` is in the table.

    Same-state transitions are not in the table and are therefore rejected: a
    second "Complete" click is a conflict, not a silent no-op (PLAN §5).
    """
    return to in TRANSITIONS.get(from_, frozenset())


def assert_can_transition(from_: WorkItemStatus, to: WorkItemStatus) -> None:
    """Raise `InvalidTransition` unless `from_ → to` is allowed."""
    if not can_transition(from_, to):
        raise InvalidTransition(from_, to)


def is_manual_transition(from_: WorkItemStatus, to: WorkItemStatus) -> bool:
    """True if an operator may request this transition through PATCH /status."""
    return (from_, to) in MANUAL_TRANSITIONS


def allowed_actions(status: WorkItemStatus, *, attempts_remaining: int) -> list[str]:
    """The actions an operator may take on an item in `status`.

    `attempts_remaining` is what is left of `AI_MAX_ATTEMPTS`. A failed item
    that has used them all offers no retry button, because the API would
    reject the call with MAX_ATTEMPTS_REACHED.
    """
    if status is S.RECEIVED:
        return [ACTION_ANALYSE]
    if status is S.FAILED:
        return [ACTION_RETRY] if attempts_remaining > 0 else []
    if status is S.READY_FOR_REVIEW:
        return [ACTION_COMPLETE]
    # ANALYSING: work is in flight. COMPLETED: terminal.
    return []
