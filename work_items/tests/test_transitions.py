"""BE-01 / PLAN §10 #4: the state machine, exhaustively.

These are pure unit tests: no database, no Django test client.
"""

from __future__ import annotations

import itertools

import pytest

from work_items.domain.errors import InvalidTransition
from work_items.domain.status import WorkItemStatus as S
from work_items.domain.transitions import (
    ACTION_ANALYSE,
    ACTION_COMPLETE,
    ACTION_RETRY,
    MANUAL_TRANSITIONS,
    allowed_actions,
    assert_can_transition,
    can_transition,
    is_manual_transition,
)

# The five transitions PLAN §5 allows. Everything else must be rejected.
ALLOWED_PAIRS = {
    (S.RECEIVED, S.ANALYSING),
    (S.ANALYSING, S.READY_FOR_REVIEW),
    (S.ANALYSING, S.FAILED),
    (S.FAILED, S.ANALYSING),
    (S.READY_FOR_REVIEW, S.COMPLETED),
}

ALL_PAIRS = list(itertools.product(S, S))


def test_the_table_covers_every_from_and_to_combination() -> None:
    assert len(ALL_PAIRS) == 25


@pytest.mark.parametrize(("from_", "to"), ALL_PAIRS, ids=lambda s: str(s))
def test_only_the_five_documented_pairs_are_allowed(from_: S, to: S) -> None:
    expected = (from_, to) in ALLOWED_PAIRS
    assert can_transition(from_, to) is expected

    if expected:
        assert_can_transition(from_, to)  # must not raise
    else:
        with pytest.raises(InvalidTransition) as exc_info:
            assert_can_transition(from_, to)
        assert exc_info.value.current_status == from_.value
        assert exc_info.value.to_status == to.value
        assert exc_info.value.code == "INVALID_TRANSITION"


@pytest.mark.parametrize("status", list(S))
def test_same_state_transitions_are_rejected(status: S) -> None:
    # A second "Complete" click is a conflict, not a silent no-op (PLAN §5).
    assert can_transition(status, status) is False


def test_completed_is_terminal() -> None:
    assert not any(can_transition(S.COMPLETED, to) for to in S)


def test_only_ready_for_review_to_completed_is_manual() -> None:
    assert set(MANUAL_TRANSITIONS) == {(S.READY_FOR_REVIEW, S.COMPLETED)}
    assert is_manual_transition(S.READY_FOR_REVIEW, S.COMPLETED) is True
    # System-only: reaching these requires actually running an analysis.
    assert is_manual_transition(S.RECEIVED, S.ANALYSING) is False
    assert is_manual_transition(S.ANALYSING, S.READY_FOR_REVIEW) is False
    assert is_manual_transition(S.FAILED, S.ANALYSING) is False


def test_every_manual_transition_is_also_in_the_transition_table() -> None:
    for from_, to in MANUAL_TRANSITIONS:
        assert can_transition(from_, to)


@pytest.mark.parametrize(
    ("status", "attempts_remaining", "expected"),
    [
        (S.RECEIVED, 5, [ACTION_ANALYSE]),
        (S.RECEIVED, 0, [ACTION_ANALYSE]),
        (S.FAILED, 3, [ACTION_RETRY]),
        (S.FAILED, 1, [ACTION_RETRY]),
        (S.FAILED, 0, []),
        (S.READY_FOR_REVIEW, 5, [ACTION_COMPLETE]),
        (S.READY_FOR_REVIEW, 0, [ACTION_COMPLETE]),
        (S.ANALYSING, 5, []),
        (S.COMPLETED, 5, []),
    ],
)
def test_allowed_actions(status: S, attempts_remaining: int, expected: list[str]) -> None:
    assert allowed_actions(status, attempts_remaining=attempts_remaining) == expected
