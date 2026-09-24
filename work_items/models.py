"""Persistence layer (PLAN §4).

Three tables, with a deliberate split of responsibilities:

- `WorkItem` holds only the **latest valid** analysis. Invalid model output
  never reaches it.
- `AnalysisAttempt` keeps every call, including the ones that returned garbage,
  so a failure can still be debugged.
- `StatusTransition` is the audit trail, written in the same transaction as
  each status change.

The constraints below are the backstop: if a service ever has a bug, the
database refuses the write rather than storing a half-analysed item.
"""

from __future__ import annotations

import uuid

from django.db import models

from .domain.status import AttemptOutcome, TransitionActor, WorkItemStatus
from .domain.transitions import allowed_actions

#: Raw model output is kept for debugging only, never shown as a result.
RAW_OUTPUT_MAX_CHARS = 4096


class WorkItem(models.Model):
    # A UUID primary key is not guessable, so it is safe to expose in URLs and
    # to let the external system reference.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # The idempotency key from the source system. The unique constraint below
    # is what actually makes concurrent intake safe (PLAN §6).
    external_id = models.CharField(max_length=100)
    title = models.CharField(max_length=200)
    description = models.TextField()

    # sha256 of the canonical JSON of {title, description}: lets us tell a
    # harmless replay from a conflicting one.
    content_hash = models.CharField(max_length=64)

    status = models.CharField(
        max_length=32,
        choices=WorkItemStatus.choices(),
        default=WorkItemStatus.RECEIVED,
        db_index=True,
    )

    # --- Latest valid AI result. Columns, not JSON, so they are filterable. ---
    category = models.CharField(max_length=32, null=True, blank=True)
    priority = models.CharField(max_length=16, null=True, blank=True)
    summary = models.TextField(null=True, blank=True)
    recommended_action = models.TextField(null=True, blank=True)
    # Which model produced the result above; part of its provenance.
    analysis_model = models.CharField(max_length=100, null=True, blank=True)
    analysed_at = models.DateTimeField(null=True, blank=True)

    # Set when an analysis starts, so a crashed run can be detected and reaped.
    analysis_started_at = models.DateTimeField(null=True, blank=True)

    last_error_code = models.CharField(max_length=50, null=True, blank=True)
    last_error_message = models.TextField(null=True, blank=True)

    attempt_count = models.PositiveIntegerField(default=0)
    # Incremented on every status change and exposed to the client, so a stale
    # UI can tell that what it is looking at has moved on.
    version = models.PositiveIntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    # `auto_now` is skipped by QuerySet.update(), which is why
    # workflow.transition() sets this column explicitly (PLAN §5).
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        # `id` breaks ties. `created_at` alone is not a total order, and
        # PostgreSQL makes no promise about the order of rows it cannot
        # distinguish — under LIMIT/OFFSET that lets a row appear on two pages
        # or on none. Two items created in the same microsecond are rare but
        # not impossible, and a paginated list must never lose one.
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["external_id"], name="uniq_work_item_external_id"),
            models.CheckConstraint(
                condition=models.Q(status__in=WorkItemStatus.values()),
                name="work_item_valid_status",
            ),
            # An item may only be reviewable or completed if it carries a full
            # analysis. This is what makes "invalid AI output cannot corrupt a
            # work item" true even if a service is buggy.
            models.CheckConstraint(
                condition=~models.Q(
                    status__in=[WorkItemStatus.READY_FOR_REVIEW, WorkItemStatus.COMPLETED]
                )
                | models.Q(
                    category__isnull=False,
                    priority__isnull=False,
                    summary__isnull=False,
                    recommended_action__isnull=False,
                ),
                name="work_item_reviewable_has_analysis",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="work_item_status_created"),
            # The reaper runs on a timer and asks one question: which analyses
            # started too long ago? A partial index answers it directly and
            # only ever holds the items currently in flight, so it stays a few
            # pages wide no matter how large the table grows.
            models.Index(
                fields=["analysis_started_at"],
                name="work_item_analysing_started",
                condition=models.Q(status=str(WorkItemStatus.ANALYSING)),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.external_id} ({self.status})"

    @property
    def domain_status(self) -> WorkItemStatus:
        return WorkItemStatus(self.status)

    @property
    def has_analysis(self) -> bool:
        return all(
            value is not None
            for value in (self.category, self.priority, self.summary, self.recommended_action)
        )

    def attempts_remaining(self, max_attempts: int) -> int:
        return max(max_attempts - self.attempt_count, 0)

    def allowed_actions(self, max_attempts: int) -> list[str]:
        """Delegates to the domain, so workflow rules live in exactly one place."""
        return allowed_actions(
            self.domain_status,
            attempts_remaining=self.attempts_remaining(max_attempts),
        )


class AnalysisAttempt(models.Model):
    """One row per LLM call, successful or not (PLAN §4)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_item = models.ForeignKey(WorkItem, related_name="attempts", on_delete=models.CASCADE)
    attempt_no = models.PositiveIntegerField()

    provider = models.CharField(max_length=50)
    model = models.CharField(max_length=100, blank=True)
    # Stored per attempt so a result can be reproduced against the prompt that
    # actually produced it.
    prompt_version = models.CharField(max_length=20)

    outcome = models.CharField(max_length=16, choices=AttemptOutcome.choices())
    error_code = models.CharField(max_length=50, null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    # Truncated; for debugging only, never surfaced as an analysis result.
    raw_output = models.TextField(null=True, blank=True)

    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["work_item", "attempt_no"], name="uniq_attempt_no_per_work_item"
            ),
        ]
        indexes = [models.Index(fields=["work_item", "-started_at"], name="attempt_item_started")]

    def __str__(self) -> str:
        return f"attempt {self.attempt_no} of {self.work_item_id}: {self.outcome}"


class StatusTransition(models.Model):
    """Audit trail: every status change, written in the same transaction."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_item = models.ForeignKey(WorkItem, related_name="transitions", on_delete=models.CASCADE)
    from_status = models.CharField(max_length=32)
    to_status = models.CharField(max_length=32)
    actor = models.CharField(max_length=16, choices=TransitionActor.choices())
    reason = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["work_item", "-created_at"], name="transition_item_created")
        ]

    def __str__(self) -> str:
        return f"{self.from_status} → {self.to_status} by {self.actor}"
