"""BE-04 / PLAN §10 #1-#3: idempotent intake, including under real concurrency."""

from __future__ import annotations

import threading

import pytest
from django.db import connection

from work_items.domain.errors import DuplicateConflict
from work_items.domain.status import WorkItemStatus
from work_items.models import WorkItem
from work_items.services.intake import content_hash_for, create_or_get_work_item

PAYLOAD = {
    "external_id": "CRM-12345",
    "title": "Missing income document",
    "description": "The applicant submitted their application but no payslip was attached.",
}


@pytest.mark.django_db
def test_first_submission_creates_the_item() -> None:
    result = create_or_get_work_item(**PAYLOAD)

    assert result.created is True
    assert result.item.external_id == "CRM-12345"
    assert result.item.status == WorkItemStatus.RECEIVED
    assert result.item.content_hash == content_hash_for(PAYLOAD["title"], PAYLOAD["description"])
    assert WorkItem.objects.count() == 1


@pytest.mark.django_db
def test_replaying_the_same_payload_returns_the_same_row() -> None:
    first = create_or_get_work_item(**PAYLOAD)
    second = create_or_get_work_item(**PAYLOAD)

    assert second.created is False
    assert second.item.id == first.item.id
    assert WorkItem.objects.count() == 1


@pytest.mark.django_db
def test_whitespace_only_differences_are_still_a_replay() -> None:
    create_or_get_work_item(**PAYLOAD)

    result = create_or_get_work_item(
        external_id="  CRM-12345 ",
        title=f"  {PAYLOAD['title']}  ",
        description=f"\n{PAYLOAD['description']}\t",
    )

    assert result.created is False
    assert WorkItem.objects.count() == 1


@pytest.mark.django_db
def test_same_external_id_with_different_content_is_a_conflict() -> None:
    create_or_get_work_item(**PAYLOAD)

    with pytest.raises(DuplicateConflict) as exc_info:
        create_or_get_work_item(
            external_id="CRM-12345",
            title="Something else entirely",
            description="A different description.",
        )

    assert exc_info.value.code == "DUPLICATE_CONFLICT"
    assert exc_info.value.details["external_id"] == "CRM-12345"

    # The stored item must be untouched: a late replay cannot overwrite an
    # item somebody is already reviewing.
    stored = WorkItem.objects.get(external_id="CRM-12345")
    assert stored.title == PAYLOAD["title"]
    assert stored.description == PAYLOAD["description"]
    assert WorkItem.objects.count() == 1


@pytest.mark.django_db
def test_different_external_ids_are_separate_items() -> None:
    create_or_get_work_item(**PAYLOAD)
    create_or_get_work_item(**{**PAYLOAD, "external_id": "CRM-99999"})

    assert WorkItem.objects.count() == 2


def test_content_hash_ignores_key_order_and_whitespace() -> None:
    assert content_hash_for("A", "B") == content_hash_for("  A  ", "\nB\t")
    assert content_hash_for("A", "B") != content_hash_for("B", "A")


THREADS = 5


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason=(
        "Needs PostgreSQL: SQLite serialises writers, so it cannot exercise the "
        "real INSERT race that the unique constraint protects against."
    ),
)
def test_five_simultaneous_submissions_create_exactly_one_row() -> None:
    """PLAN §10 #2: the headline race.

    All five threads are released at the same instant by a barrier. Without the
    unique constraint, several of them would pass a "does it exist?" check and
    insert duplicates.
    """
    barrier = threading.Barrier(THREADS, timeout=10)
    created_flags: list[bool] = []
    failures: list[BaseException] = []
    lock = threading.Lock()

    def submit() -> None:
        try:
            barrier.wait()
            result = create_or_get_work_item(**PAYLOAD)
            with lock:
                created_flags.append(result.created)
        except BaseException as exc:  # surfaced by the assertions below
            with lock:
                failures.append(exc)
        finally:
            # Each thread owns its connection; leaking them breaks teardown.
            connection.close()

    threads = [threading.Thread(target=submit) for _ in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not failures, f"threads raised: {failures}"
    assert WorkItem.objects.filter(external_id="CRM-12345").count() == 1
    assert created_flags.count(True) == 1, "exactly one caller may win the insert"
    assert created_flags.count(False) == THREADS - 1
