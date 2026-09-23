"""Idempotent intake (PLAN §6).

The source system may deliver the same work item more than once — retries,
at-least-once queues, an operator re-running a sync. The contract:

| Situation                                | Result                          |
|------------------------------------------|---------------------------------|
| New externalId                           | created                         |
| Same externalId, same content (a replay)  | the existing item, not created  |
| Same externalId, different content        | `DuplicateConflict`, untouched  |

Note what this module does *not* do: check whether the row exists and then
insert it. Two simultaneous requests would both see "doesn't exist" and both
insert. The unique constraint on `external_id` is the only real guarantee;
`get_or_create` turns the loser's `IntegrityError` into a clean re-fetch.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..domain.errors import DuplicateConflict
from ..domain.status import WorkItemStatus
from ..models import WorkItem


@dataclass(frozen=True)
class IntakeResult:
    item: WorkItem
    created: bool


def content_hash_for(title: str, description: str) -> str:
    """sha256 over canonical JSON, so the hash is stable across key order.

    Whitespace is stripped first: "  Missing payslip " and "Missing payslip"
    are the same work item, not a conflict.
    """
    canonical = json.dumps(
        {"title": title.strip(), "description": description.strip()},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_or_get_work_item(*, external_id: str, title: str, description: str) -> IntakeResult:
    """Create the item, or return the existing one if this is a replay.

    Raises `DuplicateConflict` if the externalId is known but the content
    differs. The stored item is never modified: a late replay must not
    overwrite something an operator is already reviewing.
    """
    external_id = external_id.strip()
    title = title.strip()
    description = description.strip()
    content_hash = content_hash_for(title, description)

    item, created = WorkItem.objects.get_or_create(
        external_id=external_id,
        defaults={
            "title": title,
            "description": description,
            "content_hash": content_hash,
            "status": str(WorkItemStatus.RECEIVED),
        },
    )

    if not created and item.content_hash != content_hash:
        raise DuplicateConflict(external_id)

    return IntakeResult(item=item, created=created)
