"""Running an analysis (PLAN §7, Orchestration).

The shape of this function is the whole point:

    1. claim the item          RECEIVED|FAILED -> ANALYSING   (transaction)
    2. call the model                                          (NO transaction)
    3. parse and validate
    4. record the outcome      ANALYSING -> READY_FOR_REVIEW   (transaction)
                               ANALYSING -> FAILED             (transaction)

Step 2 sits deliberately between the transactions. Wrapping a network call
that can take twenty seconds in `transaction.atomic()` would hold a database
connection, and any row locks, for its whole duration — the classic way to
turn a slow dependency into a database outage.

Result fields are written only after validation and in the *same* UPDATE as
the status change, so an item is never READY_FOR_REVIEW with a half-written
analysis. The CHECK constraint from PLAN §4 refuses the write if that is ever
violated.

Every exit is accounted for. An `AIError` becomes a FAILED item with its code;
so does an unexpected exception, because a bug must never leave an item stuck
in ANALYSING where no operator can act on it. And step 4 may find that the item
is no longer ANALYSING at all — see `_settle`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction as db_transaction
from django.db.models import F
from django.utils import timezone

from ..ai.base import (
    STALE_ANALYSIS_CODE,
    UNEXPECTED_ERROR_CODE,
    AIError,
    AIProvider,
    WorkItemInput,
)
from ..ai.factory import get_ai_provider
from ..ai.parser import parse_analysis
from ..ai.prompt import PROMPT_VERSION
from ..ai.schema import AnalysisResult
from ..domain.errors import (
    InvalidTransition,
    MaxAttemptsReached,
    NotRetryable,
    WorkItemNotFound,
)
from ..domain.status import AttemptOutcome, TransitionActor, WorkItemStatus
from ..models import RAW_OUTPUT_MAX_CHARS, AnalysisAttempt, WorkItem
from . import workflow

logger = logging.getLogger(__name__)


def analyse_work_item(item_id: UUID | str, *, provider: AIProvider | None = None) -> WorkItem:
    """First analysis of a newly received item."""
    return run_analysis(item_id, expected_from=WorkItemStatus.RECEIVED, provider=provider)


def retry_work_item(item_id: UUID | str, *, provider: AIProvider | None = None) -> WorkItem:
    """Re-run an analysis that failed.

    Retrying is only meaningful from FAILED, and only while attempts remain:
    each one costs a model call, so an item that keeps failing escalates to a
    human instead of looping forever.
    """
    item = _get_item(item_id)

    if item.domain_status is not WorkItemStatus.FAILED:
        raise NotRetryable(item.status)

    max_attempts = settings.AI_MAX_ATTEMPTS
    if item.attempt_count >= max_attempts:
        raise MaxAttemptsReached(item.attempt_count, max_attempts)

    return run_analysis(item_id, expected_from=WorkItemStatus.FAILED, provider=provider)


def run_analysis(
    item_id: UUID | str,
    *,
    expected_from: WorkItemStatus,
    provider: AIProvider | None = None,
) -> WorkItem:
    """Analyse one item. Returns it READY_FOR_REVIEW or FAILED.

    Raises `InvalidTransition` if the item is not in `expected_from` — two
    operators clicking "Analyse" at once means one of them gets a conflict and
    the model is called exactly once.
    """
    provider = provider or get_ai_provider()
    started_at = timezone.now()

    item, attempt = _claim(item_id, expected_from=expected_from, provider=provider, at=started_at)

    # --- Outside any transaction: no connection held across the network call.
    raw: str | dict | None = None
    try:
        raw = provider.analyse(
            WorkItemInput(
                external_id=item.external_id,
                title=item.title,
                description=item.description,
            ),
            timeout=settings.AI_TIMEOUT_SECONDS,
        )
        result = parse_analysis(raw)
    except AIError as exc:
        return _record_failure(
            item,
            attempt,
            code=exc.code,
            message=exc.message,
            raw=exc.raw_output if exc.raw_output is not None else raw,
            started_at=started_at,
        )
    except Exception as exc:
        # A bug in our code, not a known failure mode. Log the stack trace,
        # but still leave the item in a state an operator can retry from.
        logger.exception("Unexpected error analysing work item %s", item.id)
        return _record_failure(
            item,
            attempt,
            code=UNEXPECTED_ERROR_CODE,
            message=f"{type(exc).__name__}: {exc}",
            raw=raw,
            started_at=started_at,
        )

    return _record_success(item, attempt, result, provider=provider, started_at=started_at)


# --- steps -----------------------------------------------------------------


def _settle(
    item_id: UUID | str,
    *,
    to_status: WorkItemStatus,
    reason: str,
    **fields: object,
) -> WorkItem:
    """Record the outcome, or accept that the item moved on without us.

    Between the claim and here the item spent the whole model call outside any
    transaction — long enough for the reaper to declare it abandoned (PLAN §5).
    When that happens the compare-and-set matches nothing, and the right answer
    is to leave the reaper's state alone: an operator may already be looking at
    the FAILED item, and a result arriving after we gave up on it must not
    resurrect the row underneath them.

    So the late result loses, but quietly and safely. Nothing about the call is
    lost — the attempt row records exactly what the model did — and the caller
    gets the item as it really is instead of a conflict for a request that was
    processed correctly.
    """
    try:
        return workflow.transition(
            item_id,
            from_status=WorkItemStatus.ANALYSING,
            to_status=to_status,
            actor=TransitionActor.SYSTEM,
            reason=reason,
            **fields,
        )
    except InvalidTransition:
        logger.warning(
            "Work item %s left ANALYSING while its analysis was running; "
            "the %s outcome was recorded on the attempt but discarded.",
            item_id,
            to_status,
        )
        return _get_item(item_id)


def _claim(
    item_id: UUID | str,
    *,
    expected_from: WorkItemStatus,
    provider: AIProvider,
    at: datetime,
) -> tuple[WorkItem, AnalysisAttempt]:
    """Compare-and-set into ANALYSING and open an attempt row.

    `analysis_started_at` is what lets `reap_stale_analyses` find items whose
    process died mid-call.
    """
    with db_transaction.atomic():
        item = workflow.transition(
            item_id,
            from_status=expected_from,
            to_status=WorkItemStatus.ANALYSING,
            actor=TransitionActor.SYSTEM,
            reason=f"analysis started ({provider.name})",
            analysis_started_at=at,
            attempt_count=F("attempt_count") + 1,
        )
        attempt = AnalysisAttempt.objects.create(
            work_item=item,
            attempt_no=item.attempt_count,
            provider=provider.name,
            model=provider.model,
            prompt_version=PROMPT_VERSION,
            # Pessimistic: if this process dies now, the row already reads
            # FAILED, which matches what the reaper will do to the item.
            outcome=AttemptOutcome.FAILED,
            error_code=UNEXPECTED_ERROR_CODE,
            error_message="Analysis did not finish.",
            started_at=at,
        )
    return item, attempt


def _record_success(
    item: WorkItem,
    attempt: AnalysisAttempt,
    result: AnalysisResult,
    *,
    provider: AIProvider,
    started_at: datetime,
) -> WorkItem:
    finished_at = timezone.now()

    attempt.outcome = AttemptOutcome.SUCCEEDED
    attempt.error_code = None
    attempt.error_message = None
    attempt.finished_at = finished_at
    attempt.latency_ms = _elapsed_ms(started_at, finished_at)
    attempt.save(
        update_fields=["outcome", "error_code", "error_message", "finished_at", "latency_ms"]
    )

    return _settle(
        item.id,
        to_status=WorkItemStatus.READY_FOR_REVIEW,
        reason=f"analysis succeeded ({provider.name})",
        category=result.category.value,
        priority=result.priority.value,
        summary=result.summary,
        recommended_action=result.recommended_action,
        analysis_model=provider.model,
        analysed_at=finished_at,
        # The item is good now; its previous failure is history.
        last_error_code=None,
        last_error_message=None,
    )


def _record_failure(
    item: WorkItem,
    attempt: AnalysisAttempt,
    *,
    code: str,
    message: str,
    raw: str | dict | None,
    started_at: datetime,
) -> WorkItem:
    finished_at = timezone.now()

    attempt.outcome = AttemptOutcome.FAILED
    attempt.error_code = code
    attempt.error_message = message
    attempt.raw_output = _truncate(raw)
    attempt.finished_at = finished_at
    attempt.latency_ms = _elapsed_ms(started_at, finished_at)
    attempt.save(
        update_fields=[
            "outcome",
            "error_code",
            "error_message",
            "raw_output",
            "finished_at",
            "latency_ms",
        ]
    )

    # Note what is *not* here: category, priority, summary and
    # recommended_action are left exactly as they were. Bad model output never
    # touches the work item's analysis.
    return _settle(
        item.id,
        to_status=WorkItemStatus.FAILED,
        reason=f"analysis failed ({code})",
        last_error_code=code,
        last_error_message=message,
    )


# --- reaping stuck analyses (PLAN §5) --------------------------------------


def stale_analysis_cutoff(older_than_seconds: float | None = None) -> datetime:
    """The moment before which an ANALYSING item must be considered dead.

    Twice the timeout: a healthy call has long since returned, so anything
    older means the process handling it went away.
    """
    seconds = (
        older_than_seconds if older_than_seconds is not None else settings.AI_TIMEOUT_SECONDS * 2
    )
    return timezone.now() - timedelta(seconds=seconds)


def reap_stale_analyses(*, older_than_seconds: float | None = None) -> int:
    """Move abandoned ANALYSING items to FAILED so they can be retried.

    Without this, a crash mid-analysis would strand an item in ANALYSING
    forever: no action is allowed from there, so no operator could rescue it.
    In production this runs as a periodic job.

    Each item is moved with the same compare-and-set as everything else, so an
    analysis that finishes a moment before the reaper gets to it simply wins,
    and the reap becomes a no-op rather than a lost result.
    """
    cutoff = stale_analysis_cutoff(older_than_seconds)

    stale_ids = list(
        WorkItem.objects.filter(
            status=WorkItemStatus.ANALYSING,
            analysis_started_at__lt=cutoff,
        ).values_list("id", flat=True)
    )

    reaped = 0
    for item_id in stale_ids:
        try:
            workflow.transition(
                item_id,
                from_status=WorkItemStatus.ANALYSING,
                to_status=WorkItemStatus.FAILED,
                actor=TransitionActor.SYSTEM,
                reason="analysis abandoned; reaped as stale",
                last_error_code=STALE_ANALYSIS_CODE,
                last_error_message=(
                    "The analysis did not finish. The process handling it most likely "
                    "stopped. Retrying is safe."
                ),
            )
            reaped += 1
        except InvalidTransition:
            # It finished between the query and the update. Nothing to fix.
            logger.info("Work item %s left ANALYSING before it could be reaped", item_id)

    return reaped


# --- helpers ---------------------------------------------------------------


def _get_item(item_id: UUID | str) -> WorkItem:
    item = WorkItem.objects.filter(pk=item_id).first()
    if item is None:
        raise WorkItemNotFound(item_id)
    return item


def _truncate(raw: str | dict | None) -> str | None:
    if raw is None:
        return None
    text = raw if isinstance(raw, str) else str(raw)
    if len(text) <= RAW_OUTPUT_MAX_CHARS:
        return text
    return text[: RAW_OUTPUT_MAX_CHARS - 3] + "..."


def _elapsed_ms(started_at: datetime, finished_at: datetime) -> int:
    return max(int((finished_at - started_at).total_seconds() * 1000), 0)
