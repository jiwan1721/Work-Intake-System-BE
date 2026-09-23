"""The only code in the system that changes `WorkItem.status` (PLAN §5).

Every status change is a *conditional* UPDATE:

    UPDATE work_items_workitem
       SET status = :to, version = version + 1, ...
     WHERE id = :id AND status = :from

If another request got there first, the WHERE clause matches zero rows and we
raise `InvalidTransition` instead of overwriting their work. The read-modify-
write version (`item.status = X; item.save()`) loses that race: two operators
clicking "Analyse" at the same moment would both read RECEIVED and both write
ANALYSING.

Compare-and-set also holds no row lock, which matters because an analysis runs
a slow network call between its two transitions.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction as db_transaction
from django.db.models import F
from django.utils import timezone

from ..domain.errors import InvalidTransition, WorkItemNotFound
from ..domain.status import TransitionActor, WorkItemStatus
from ..domain.transitions import assert_can_transition
from ..models import StatusTransition, WorkItem


def transition(
    item_id: UUID | str,
    *,
    from_status: WorkItemStatus,
    to_status: WorkItemStatus,
    actor: TransitionActor,
    reason: str = "",
    **fields: Any,
) -> WorkItem:
    """Move an item from `from_status` to `to_status`, atomically.

    `fields` are written in the same UPDATE, so an item never exists in a state
    where its status and its data disagree — the analysis result and
    READY_FOR_REVIEW land together or not at all.

    Raises `InvalidTransition` if the pair is not in the table or the row is no
    longer in `from_status`, and `WorkItemNotFound` if the row is gone.
    """
    # Cheap check first: an impossible pair never reaches the database.
    assert_can_transition(from_status, to_status)

    with db_transaction.atomic():
        now = timezone.now()
        updates: dict[str, Any] = {
            "status": str(to_status),
            "version": F("version") + 1,
            # QuerySet.update() bypasses auto_now, so set it explicitly.
            "updated_at": now,
            **fields,
        }
        if to_status is WorkItemStatus.COMPLETED:
            updates.setdefault("completed_at", now)

        matched = WorkItem.objects.filter(pk=item_id, status=str(from_status)).update(**updates)

        if matched == 0:
            current = WorkItem.objects.filter(pk=item_id).values_list("status", flat=True).first()
            if current is None:
                raise WorkItemNotFound(item_id)
            # Somebody else moved it. Report where it actually is, so the
            # client can refresh and decide what to do.
            raise InvalidTransition(current, to_status)

        StatusTransition.objects.create(
            work_item_id=item_id,
            from_status=str(from_status),
            to_status=str(to_status),
            actor=str(actor),
            reason=reason,
        )

        return WorkItem.objects.get(pk=item_id)


def complete(item_id: UUID | str, *, reason: str = "") -> WorkItem:
    """The one transition an operator may request directly (PLAN §5)."""
    return transition(
        item_id,
        from_status=WorkItemStatus.READY_FOR_REVIEW,
        to_status=WorkItemStatus.COMPLETED,
        actor=TransitionActor.OPERATOR,
        reason=reason,
    )
