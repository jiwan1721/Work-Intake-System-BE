"""BE-02: the database constraints, proved by making the database reject writes.

These are the backstop from PLAN §4. Application code is supposed to prevent
all three of these, so each test deliberately writes around the services.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from work_items.domain.status import WorkItemStatus
from work_items.models import WorkItem

from .factories import ANALYSIS_FIELDS, make_work_item

pytestmark = pytest.mark.django_db


def test_duplicate_external_id_is_rejected() -> None:
    make_work_item(external_id="CRM-12345")

    with pytest.raises(IntegrityError, match="uniq_work_item_external_id"), transaction.atomic():
        make_work_item(external_id="CRM-12345", title="A different item")

    assert WorkItem.objects.filter(external_id="CRM-12345").count() == 1


@pytest.mark.parametrize(
    "status", [WorkItemStatus.READY_FOR_REVIEW, WorkItemStatus.COMPLETED], ids=str
)
def test_reviewable_status_without_analysis_is_rejected(status: WorkItemStatus) -> None:
    with (
        pytest.raises(IntegrityError, match="work_item_reviewable_has_analysis"),
        transaction.atomic(),
    ):
        make_work_item(external_id="CRM-2", status=status, with_analysis=False)

    assert not WorkItem.objects.exists()


@pytest.mark.parametrize("missing_field", ["category", "priority", "summary", "recommended_action"])
def test_reviewable_status_with_a_partial_analysis_is_rejected(missing_field: str) -> None:
    partial = {**ANALYSIS_FIELDS, missing_field: None}

    with (
        pytest.raises(IntegrityError, match="work_item_reviewable_has_analysis"),
        transaction.atomic(),
    ):
        make_work_item(external_id="CRM-3", status=WorkItemStatus.READY_FOR_REVIEW, **partial)


def test_reviewable_status_with_a_full_analysis_is_accepted() -> None:
    item = make_work_item(
        external_id="CRM-4", status=WorkItemStatus.READY_FOR_REVIEW, with_analysis=True
    )
    assert item.has_analysis is True


def test_invalid_status_value_is_rejected() -> None:
    with pytest.raises(IntegrityError, match="work_item_valid_status"), transaction.atomic():
        make_work_item(external_id="CRM-5", status="BOGUS")

    assert not WorkItem.objects.exists()


def test_non_reviewable_statuses_may_have_no_analysis() -> None:
    # RECEIVED, ANALYSING and FAILED items carry no result, and that is fine.
    for index, status in enumerate(
        [WorkItemStatus.RECEIVED, WorkItemStatus.ANALYSING, WorkItemStatus.FAILED]
    ):
        item = make_work_item(external_id=f"CRM-ok-{index}", status=status)
        assert item.has_analysis is False


def test_defaults() -> None:
    item = make_work_item(external_id="CRM-6")

    assert item.status == WorkItemStatus.RECEIVED
    assert item.version == 1
    assert item.attempt_count == 0
    assert item.analysed_at is None
    assert item.completed_at is None
    assert item.created_at is not None


def test_attempts_remaining_never_goes_negative() -> None:
    item = make_work_item(external_id="CRM-7", attempt_count=9)
    assert item.attempts_remaining(max_attempts=5) == 0
