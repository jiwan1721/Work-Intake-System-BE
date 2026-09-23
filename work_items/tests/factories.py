"""Small helpers so tests state only what they care about."""

from __future__ import annotations

from typing import Any

from work_items.domain.status import WorkItemStatus
from work_items.models import WorkItem
from work_items.services.intake import content_hash_for

ANALYSIS_FIELDS: dict[str, Any] = {
    "category": "DOCUMENT_REQUEST",
    "priority": "HIGH",
    "summary": "The applicant needs to provide their latest payslip.",
    "recommended_action": "Request the missing payslip from the applicant.",
    "analysis_model": "mock-v1",
}


def make_work_item(
    *,
    external_id: str = "CRM-1",
    title: str = "Missing income document",
    description: str = "The applicant submitted their application but no payslip was attached.",
    status: WorkItemStatus | str = WorkItemStatus.RECEIVED,
    with_analysis: bool = False,
    **extra: Any,
) -> WorkItem:
    """Create a work item directly, bypassing the intake service.

    Tests for the intake service itself must not use this: they need to go
    through the real code path.
    """
    fields: dict[str, Any] = {
        "external_id": external_id,
        "title": title,
        "description": description,
        "content_hash": content_hash_for(title, description),
        "status": str(status),
    }
    if with_analysis:
        fields.update(ANALYSIS_FIELDS)
    fields.update(extra)
    return WorkItem.objects.create(**fields)
