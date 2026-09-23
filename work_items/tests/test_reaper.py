"""BE-09: reaping analyses whose process died."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.utils import timezone

from work_items.domain.status import WorkItemStatus
from work_items.models import StatusTransition, WorkItem
from work_items.services import workflow
from work_items.services.analysis import reap_stale_analyses

from .factories import make_work_item

pytestmark = pytest.mark.django_db

# AI_TIMEOUT_SECONDS=20 in these tests, so the cutoff is 40s.
TIMEOUT = 20


def analysing(external_id: str, *, started_seconds_ago: float | None):
    started_at = (
        None
        if started_seconds_ago is None
        else timezone.now() - timedelta(seconds=started_seconds_ago)
    )
    return make_work_item(
        external_id=external_id,
        status=WorkItemStatus.ANALYSING,
        analysis_started_at=started_at,
    )


@override_settings(AI_TIMEOUT_SECONDS=TIMEOUT)
def test_a_stale_item_is_failed_and_becomes_retryable() -> None:
    item = analysing("CRM-1", started_seconds_ago=120)

    assert reap_stale_analyses() == 1

    item.refresh_from_db()
    assert item.status == WorkItemStatus.FAILED
    assert item.last_error_code == "STALE_ANALYSIS"
    assert item.last_error_message
    assert item.allowed_actions(max_attempts=5) == ["retry"]


@override_settings(AI_TIMEOUT_SECONDS=TIMEOUT)
def test_a_fresh_analysis_is_left_alone() -> None:
    item = analysing("CRM-2", started_seconds_ago=5)

    assert reap_stale_analyses() == 0

    item.refresh_from_db()
    assert item.status == WorkItemStatus.ANALYSING
    assert item.last_error_code is None


@override_settings(AI_TIMEOUT_SECONDS=TIMEOUT)
def test_the_cutoff_is_twice_the_timeout() -> None:
    just_inside = analysing("CRM-3", started_seconds_ago=2 * TIMEOUT - 5)
    just_outside = analysing("CRM-4", started_seconds_ago=2 * TIMEOUT + 5)

    assert reap_stale_analyses() == 1

    just_inside.refresh_from_db()
    just_outside.refresh_from_db()
    assert just_inside.status == WorkItemStatus.ANALYSING
    assert just_outside.status == WorkItemStatus.FAILED


@override_settings(AI_TIMEOUT_SECONDS=TIMEOUT)
def test_items_in_other_statuses_are_never_touched() -> None:
    long_ago = timezone.now() - timedelta(days=1)
    received = make_work_item(external_id="CRM-5", analysis_started_at=long_ago)
    reviewable = make_work_item(
        external_id="CRM-6",
        status=WorkItemStatus.READY_FOR_REVIEW,
        with_analysis=True,
        analysis_started_at=long_ago,
    )

    assert reap_stale_analyses() == 0

    received.refresh_from_db()
    reviewable.refresh_from_db()
    assert received.status == WorkItemStatus.RECEIVED
    assert reviewable.status == WorkItemStatus.READY_FOR_REVIEW


@override_settings(AI_TIMEOUT_SECONDS=TIMEOUT)
def test_an_analysis_that_finishes_first_makes_the_reap_a_no_op(monkeypatch) -> None:
    """The compare-and-set guarantee, from the reaper's side.

    The item is selected as stale, then finishes before the UPDATE lands. The
    reaper must not overwrite the result it just produced.
    """
    item = analysing("CRM-7", started_seconds_ago=120)
    real_transition = workflow.transition

    def finish_first(item_id, **kwargs):
        # The reaper has already selected this row as stale. The analysis
        # lands *now*, before the reaper's UPDATE, so the UPDATE will match
        # zero rows and raise InvalidTransition.
        WorkItem.objects.filter(pk=item_id, status=WorkItemStatus.ANALYSING).update(
            status=WorkItemStatus.READY_FOR_REVIEW,
            category="DOCUMENT_REQUEST",
            priority="HIGH",
            summary="Needs a payslip.",
            recommended_action="Request the payslip.",
        )
        return real_transition(item_id, **kwargs)

    monkeypatch.setattr(workflow, "transition", finish_first)

    # The loser is swallowed: nothing was reaped, and nothing was corrupted.
    assert reap_stale_analyses() == 0

    item.refresh_from_db()
    assert item.status == WorkItemStatus.READY_FOR_REVIEW
    assert item.summary == "Needs a payslip."
    assert item.last_error_code is None
    assert not StatusTransition.objects.filter(
        work_item=item, to_status=WorkItemStatus.FAILED
    ).exists()


@override_settings(AI_TIMEOUT_SECONDS=TIMEOUT)
def test_reaping_writes_an_audit_row() -> None:
    item = analysing("CRM-8", started_seconds_ago=120)

    reap_stale_analyses()

    audit = StatusTransition.objects.get(work_item=item)
    assert audit.from_status == WorkItemStatus.ANALYSING
    assert audit.to_status == WorkItemStatus.FAILED
    assert "stale" in audit.reason


@override_settings(AI_TIMEOUT_SECONDS=TIMEOUT)
def test_an_explicit_threshold_overrides_the_default() -> None:
    item = analysing("CRM-9", started_seconds_ago=10)

    assert reap_stale_analyses(older_than_seconds=5) == 1

    item.refresh_from_db()
    assert item.status == WorkItemStatus.FAILED


@override_settings(AI_TIMEOUT_SECONDS=TIMEOUT)
def test_the_command_reports_what_it_did() -> None:
    analysing("CRM-10", started_seconds_ago=120)
    out = StringIO()

    call_command("reap_stale_analyses", stdout=out)

    assert "Reaped 1" in out.getvalue()


@override_settings(AI_TIMEOUT_SECONDS=TIMEOUT)
def test_the_command_reports_when_there_is_nothing_to_do() -> None:
    out = StringIO()

    call_command("reap_stale_analyses", stdout=out)

    assert "No stale analyses" in out.getvalue()


def test_the_command_rejects_a_negative_threshold() -> None:
    with pytest.raises(CommandError):
        call_command("reap_stale_analyses", "--older-than-seconds", "-1")
