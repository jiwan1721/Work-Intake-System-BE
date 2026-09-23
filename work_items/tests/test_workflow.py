"""BE-03: compare-and-set transitions."""

from __future__ import annotations

import uuid

import pytest

from work_items.domain.errors import InvalidTransition, WorkItemNotFound
from work_items.domain.status import TransitionActor, WorkItemStatus
from work_items.models import StatusTransition, WorkItem
from work_items.services import workflow

from .factories import ANALYSIS_FIELDS, make_work_item

pytestmark = pytest.mark.django_db


def test_transition_updates_status_version_and_audit_trail() -> None:
    item = make_work_item(external_id="CRM-1")
    before = item.updated_at

    updated = workflow.transition(
        item.id,
        from_status=WorkItemStatus.RECEIVED,
        to_status=WorkItemStatus.ANALYSING,
        actor=TransitionActor.SYSTEM,
        reason="operator clicked analyse",
    )

    assert updated.status == WorkItemStatus.ANALYSING
    assert updated.version == item.version + 1
    assert updated.updated_at > before

    audit = StatusTransition.objects.get(work_item=item)
    assert audit.from_status == WorkItemStatus.RECEIVED
    assert audit.to_status == WorkItemStatus.ANALYSING
    assert audit.actor == TransitionActor.SYSTEM
    assert audit.reason == "operator clicked analyse"


def test_transition_writes_extra_fields_in_the_same_update() -> None:
    item = make_work_item(external_id="CRM-2", status=WorkItemStatus.ANALYSING)

    updated = workflow.transition(
        item.id,
        from_status=WorkItemStatus.ANALYSING,
        to_status=WorkItemStatus.READY_FOR_REVIEW,
        actor=TransitionActor.SYSTEM,
        **ANALYSIS_FIELDS,
    )

    # The result and the status land together; the DB constraint would reject
    # READY_FOR_REVIEW without them.
    assert updated.category == ANALYSIS_FIELDS["category"]
    assert updated.summary == ANALYSIS_FIELDS["summary"]
    assert updated.has_analysis is True


def test_completing_sets_completed_at() -> None:
    item = make_work_item(
        external_id="CRM-3", status=WorkItemStatus.READY_FOR_REVIEW, with_analysis=True
    )

    updated = workflow.complete(item.id, reason="reviewed by operator")

    assert updated.status == WorkItemStatus.COMPLETED
    assert updated.completed_at is not None
    assert StatusTransition.objects.get(work_item=item).actor == TransitionActor.OPERATOR


def test_pair_outside_the_table_is_rejected_before_touching_the_database() -> None:
    item = make_work_item(external_id="CRM-4", status=WorkItemStatus.RECEIVED)

    with pytest.raises(InvalidTransition) as exc_info:
        workflow.transition(
            item.id,
            from_status=WorkItemStatus.RECEIVED,
            to_status=WorkItemStatus.COMPLETED,
            actor=TransitionActor.OPERATOR,
        )

    assert exc_info.value.current_status == WorkItemStatus.RECEIVED
    item.refresh_from_db()
    assert item.status == WorkItemStatus.RECEIVED
    assert item.version == 1
    assert not StatusTransition.objects.exists()


def test_stale_from_status_reports_where_the_item_actually_is() -> None:
    # Somebody else already moved it to ANALYSING.
    item = make_work_item(external_id="CRM-5", status=WorkItemStatus.ANALYSING)

    with pytest.raises(InvalidTransition) as exc_info:
        workflow.transition(
            item.id,
            from_status=WorkItemStatus.RECEIVED,
            to_status=WorkItemStatus.ANALYSING,
            actor=TransitionActor.SYSTEM,
        )

    assert exc_info.value.current_status == WorkItemStatus.ANALYSING
    assert exc_info.value.details["currentStatus"] == WorkItemStatus.ANALYSING

    item.refresh_from_db()
    assert item.version == 1
    assert not StatusTransition.objects.exists()


def test_stale_from_status_writes_no_fields() -> None:
    item = make_work_item(external_id="CRM-6", status=WorkItemStatus.FAILED)

    with pytest.raises(InvalidTransition):
        workflow.transition(
            item.id,
            from_status=WorkItemStatus.ANALYSING,
            to_status=WorkItemStatus.READY_FOR_REVIEW,
            actor=TransitionActor.SYSTEM,
            **ANALYSIS_FIELDS,
        )

    item.refresh_from_db()
    assert item.category is None
    assert item.summary is None


def test_missing_item_raises_not_found() -> None:
    with pytest.raises(WorkItemNotFound):
        workflow.transition(
            uuid.uuid4(),
            from_status=WorkItemStatus.RECEIVED,
            to_status=WorkItemStatus.ANALYSING,
            actor=TransitionActor.SYSTEM,
        )

    assert not StatusTransition.objects.exists()


def test_only_one_of_two_racing_transitions_wins() -> None:
    """The compare-and-set guarantee, without threads.

    Both callers believe the item is RECEIVED. The second one's UPDATE matches
    no rows, which is exactly what a real race looks like.
    """
    item = make_work_item(external_id="CRM-7")

    first = workflow.transition(
        item.id,
        from_status=WorkItemStatus.RECEIVED,
        to_status=WorkItemStatus.ANALYSING,
        actor=TransitionActor.SYSTEM,
    )
    assert first.status == WorkItemStatus.ANALYSING

    with pytest.raises(InvalidTransition):
        workflow.transition(
            item.id,
            from_status=WorkItemStatus.RECEIVED,
            to_status=WorkItemStatus.ANALYSING,
            actor=TransitionActor.SYSTEM,
        )

    assert WorkItem.objects.get(pk=item.id).version == 2
    assert StatusTransition.objects.filter(work_item=item).count() == 1
