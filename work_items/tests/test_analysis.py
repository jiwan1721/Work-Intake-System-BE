"""BE-07 / PLAN §10 #6-#9: orchestration, and what happens when the model misbehaves."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.db import connection
from django.test import override_settings

from work_items.ai.base import (
    AIError,
    AIInvalidOutputError,
    AIProviderError,
    AITimeoutError,
    WorkItemInput,
)
from work_items.domain.errors import (
    InvalidTransition,
    MaxAttemptsReached,
    NotRetryable,
    WorkItemNotFound,
)
from work_items.domain.status import AttemptOutcome, WorkItemStatus
from work_items.models import AnalysisAttempt, StatusTransition, WorkItem
from work_items.services.analysis import analyse_work_item, retry_work_item, run_analysis

from .factories import make_work_item

pytestmark = pytest.mark.django_db

GOOD_JSON = (
    '{"category":"DOCUMENT_REQUEST","priority":"HIGH",'
    '"summary":"Needs a payslip.","recommendedAction":"Request the payslip."}'
)


class FakeProvider:
    """A provider the test controls completely."""

    name = "fake"
    model = "fake-v1"

    def __init__(self, *, returns: Any = GOOD_JSON, raises: Exception | None = None) -> None:
        self.returns = returns
        self.raises = raises
        self.calls: list[WorkItemInput] = []

    def analyse(self, item: WorkItemInput, *, timeout: float) -> str | dict:
        self.calls.append(item)
        if self.raises is not None:
            raise self.raises
        return self.returns


# --- Success ---------------------------------------------------------------


def test_successful_analysis_writes_the_result_and_moves_to_ready_for_review() -> None:
    item = make_work_item(external_id="CRM-1")
    provider = FakeProvider()

    updated = analyse_work_item(item.id, provider=provider)

    assert updated.status == WorkItemStatus.READY_FOR_REVIEW
    assert updated.category == "DOCUMENT_REQUEST"
    assert updated.priority == "HIGH"
    assert updated.summary == "Needs a payslip."
    assert updated.recommended_action == "Request the payslip."
    assert updated.analysis_model == "fake-v1"
    assert updated.analysed_at is not None
    assert updated.attempt_count == 1
    assert updated.last_error_code is None


def test_successful_analysis_records_a_succeeded_attempt() -> None:
    item = make_work_item(external_id="CRM-2")

    analyse_work_item(item.id, provider=FakeProvider())

    attempt = AnalysisAttempt.objects.get(work_item=item)
    assert attempt.outcome == AttemptOutcome.SUCCEEDED
    assert attempt.attempt_no == 1
    assert attempt.provider == "fake"
    assert attempt.model == "fake-v1"
    assert attempt.prompt_version
    assert attempt.latency_ms is not None
    assert attempt.finished_at is not None
    assert attempt.error_code is None


def test_the_provider_sees_the_item_text() -> None:
    item = make_work_item(external_id="CRM-3", title="Missing payslip")
    provider = FakeProvider()

    analyse_work_item(item.id, provider=provider)

    assert provider.calls[0].external_id == "CRM-3"
    assert provider.calls[0].title == "Missing payslip"


def test_both_transitions_are_audited() -> None:
    item = make_work_item(external_id="CRM-4")

    analyse_work_item(item.id, provider=FakeProvider())

    pairs = list(
        StatusTransition.objects.filter(work_item=item)
        .order_by("created_at")
        .values_list("from_status", "to_status")
    )
    assert pairs == [
        (WorkItemStatus.RECEIVED, WorkItemStatus.ANALYSING),
        (WorkItemStatus.ANALYSING, WorkItemStatus.READY_FOR_REVIEW),
    ]


# --- Failure modes (PLAN §10 #6-#9) ----------------------------------------


@pytest.mark.parametrize(
    ("provider", "expected_code"),
    [
        (FakeProvider(returns="I'm sorry, I can't help with that."), "INVALID_OUTPUT"),
        (
            FakeProvider(
                returns='{"category":"SPAM","priority":"LOW","summary":"a","recommendedAction":"b"}'
            ),
            "INVALID_OUTPUT",
        ),
        (FakeProvider(raises=AITimeoutError("too slow")), "TIMEOUT"),
        (FakeProvider(raises=AIProviderError("503")), "PROVIDER_ERROR"),
        (FakeProvider(raises=RuntimeError("something exploded")), "UNEXPECTED_ERROR"),
    ],
    ids=["not-json", "bad-enum", "timeout", "provider-error", "unexpected"],
)
def test_every_failure_mode_lands_on_failed_with_its_code(
    provider: FakeProvider, expected_code: str
) -> None:
    item = make_work_item(external_id="CRM-5")

    updated = analyse_work_item(item.id, provider=provider)

    assert updated.status == WorkItemStatus.FAILED
    assert updated.last_error_code == expected_code
    assert updated.last_error_message

    # The headline guarantee: garbage in, nothing written.
    assert updated.category is None
    assert updated.priority is None
    assert updated.summary is None
    assert updated.recommended_action is None
    assert updated.analysed_at is None

    attempt = AnalysisAttempt.objects.get(work_item=item)
    assert attempt.outcome == AttemptOutcome.FAILED
    assert attempt.error_code == expected_code


def test_a_bug_never_leaves_an_item_stuck_in_analysing() -> None:
    item = make_work_item(external_id="CRM-6")

    updated = analyse_work_item(item.id, provider=FakeProvider(raises=RuntimeError("boom")))

    assert updated.status == WorkItemStatus.FAILED
    assert WorkItem.objects.get(pk=item.id).status == WorkItemStatus.FAILED


def test_the_raw_answer_is_kept_on_the_attempt_for_debugging() -> None:
    item = make_work_item(external_id="CRM-7")
    rambling = "Well, it seems like the applicant might need something."

    analyse_work_item(item.id, provider=FakeProvider(returns=rambling))

    assert AnalysisAttempt.objects.get(work_item=item).raw_output == rambling


def test_raw_output_is_truncated_to_four_kilobytes() -> None:
    item = make_work_item(external_id="CRM-8")

    analyse_work_item(item.id, provider=FakeProvider(returns="x" * 10_000))

    raw = AnalysisAttempt.objects.get(work_item=item).raw_output
    assert raw is not None
    assert len(raw) <= 4096


def test_an_existing_analysis_survives_a_later_failure() -> None:
    """A failed re-analysis must not wipe a result an operator already has."""
    item = make_work_item(external_id="CRM-9")
    analyse_work_item(item.id, provider=FakeProvider())

    # Force the item back to FAILED as if something went wrong afterwards,
    # then retry with a broken provider.
    WorkItem.objects.filter(pk=item.id).update(status=WorkItemStatus.FAILED)
    updated = retry_work_item(item.id, provider=FakeProvider(raises=AIProviderError("503")))

    assert updated.status == WorkItemStatus.FAILED
    assert updated.category == "DOCUMENT_REQUEST"
    assert updated.summary == "Needs a payslip."


# --- Transactions ----------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_the_model_is_never_called_inside_a_transaction() -> None:
    """PLAN §7: holding a connection across a 20s network call is the bug we avoid."""
    observed: list[bool] = []

    class ConnectionCheckingProvider(FakeProvider):
        def analyse(self, item: WorkItemInput, *, timeout: float) -> str | dict:
            observed.append(connection.in_atomic_block)
            return super().analyse(item, timeout=timeout)

    item = make_work_item(external_id="CRM-10")

    analyse_work_item(item.id, provider=ConnectionCheckingProvider())

    assert observed == [False], "the provider ran inside transaction.atomic()"


# --- Analyse eligibility ---------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [WorkItemStatus.ANALYSING, WorkItemStatus.READY_FOR_REVIEW, WorkItemStatus.COMPLETED],
    ids=str,
)
def test_analyse_is_rejected_unless_the_item_is_received(status: WorkItemStatus) -> None:
    item = make_work_item(
        external_id="CRM-11",
        status=status,
        with_analysis=status in {WorkItemStatus.READY_FOR_REVIEW, WorkItemStatus.COMPLETED},
    )
    provider = FakeProvider()

    with pytest.raises(InvalidTransition):
        analyse_work_item(item.id, provider=provider)

    assert provider.calls == [], "a rejected request must not reach the model"


def test_two_racing_analyse_calls_call_the_model_once() -> None:
    item = make_work_item(external_id="CRM-12")
    first = FakeProvider()
    second = FakeProvider()

    analyse_work_item(item.id, provider=first)
    with pytest.raises(InvalidTransition):
        analyse_work_item(item.id, provider=second)

    assert len(first.calls) == 1
    assert second.calls == []
    assert AnalysisAttempt.objects.filter(work_item=item).count() == 1


def test_missing_item_is_reported_as_not_found() -> None:
    with pytest.raises(WorkItemNotFound):
        run_analysis(uuid.uuid4(), expected_from=WorkItemStatus.RECEIVED, provider=FakeProvider())


# --- Retry -----------------------------------------------------------------


def test_retry_succeeds_after_a_failure() -> None:
    item = make_work_item(external_id="CRM-13")
    analyse_work_item(item.id, provider=FakeProvider(raises=AITimeoutError("too slow")))

    updated = retry_work_item(item.id, provider=FakeProvider())

    assert updated.status == WorkItemStatus.READY_FOR_REVIEW
    assert updated.attempt_count == 2
    assert updated.last_error_code is None
    assert AnalysisAttempt.objects.filter(work_item=item).count() == 2

    outcomes = list(
        AnalysisAttempt.objects.filter(work_item=item)
        .order_by("attempt_no")
        .values_list("attempt_no", "outcome")
    )
    assert outcomes == [(1, AttemptOutcome.FAILED), (2, AttemptOutcome.SUCCEEDED)]


@pytest.mark.parametrize(
    "status",
    [WorkItemStatus.RECEIVED, WorkItemStatus.ANALYSING, WorkItemStatus.COMPLETED],
    ids=str,
)
def test_retry_is_rejected_unless_the_item_failed(status: WorkItemStatus) -> None:
    item = make_work_item(
        external_id="CRM-14",
        status=status,
        with_analysis=status is WorkItemStatus.COMPLETED,
    )
    provider = FakeProvider()

    with pytest.raises(NotRetryable) as exc_info:
        retry_work_item(item.id, provider=provider)

    assert exc_info.value.details["currentStatus"] == status
    assert provider.calls == []


@override_settings(AI_MAX_ATTEMPTS=2)
def test_retry_stops_at_the_attempt_cap() -> None:
    item = make_work_item(external_id="CRM-15")
    broken = FakeProvider(raises=AIProviderError("503"))

    analyse_work_item(item.id, provider=broken)  # attempt 1
    retry_work_item(item.id, provider=broken)  # attempt 2

    with pytest.raises(MaxAttemptsReached) as exc_info:
        retry_work_item(item.id, provider=broken)

    assert exc_info.value.details["maxAttempts"] == 2
    assert exc_info.value.details["attemptCount"] == 2
    assert len(broken.calls) == 2, "the cap must be enforced before calling the model"


@override_settings(AI_MAX_ATTEMPTS=1)
def test_an_item_at_the_cap_offers_no_retry_action() -> None:
    item = make_work_item(external_id="CRM-16")
    analyse_work_item(item.id, provider=FakeProvider(raises=AIProviderError("503")))

    assert WorkItem.objects.get(pk=item.id).allowed_actions(max_attempts=1) == []


def test_error_subclasses_all_carry_a_code() -> None:
    for error in (AIError, AITimeoutError, AIProviderError, AIInvalidOutputError):
        assert error.code
