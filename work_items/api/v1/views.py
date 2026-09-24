"""v1 HTTP layer (PLAN §8).

Views do four things and nothing else: parse the request, call a service,
serialize the result, and let the exception handler turn domain errors into
the error envelope. There is no business logic here — no status is assigned,
no hash computed, no provider chosen.

Two design decisions worth knowing when reading this file:

- **Actions are their own endpoints**, not a PATCH of `status` to ANALYSING.
  Analysis has side effects (a model call, an attempt row, a cost); a status
  PATCH should express a human decision, not trigger hidden work.
- **/analyse returns 200 even when the analysis failed.** The request was
  processed correctly and the new state is persisted. A 5xx would claim our
  server broke, which is a different thing an operator would escalate
  differently.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Prefetch, QuerySet
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status as http_status
from rest_framework.generics import ListCreateAPIView, RetrieveAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ...domain.errors import DomainError, WorkItemNotFound
from ...domain.status import TransitionActor, WorkItemStatus
from ...domain.transitions import is_manual_transition
from ...models import AnalysisAttempt, StatusTransition, WorkItem
from ...services import workflow
from ...services.analysis import analyse_work_item, retry_work_item
from ...services.intake import create_or_get_work_item
from ..permissions import HasIntakeApiKey
from .serializers import (
    StatusPatchSerializer,
    WorkItemDetailSerializer,
    WorkItemIntakeSerializer,
    WorkItemSerializer,
)


class InvalidStatusFilter(DomainError):
    code = "VALIDATION_ERROR"
    http_status = http_status.HTTP_400_BAD_REQUEST


class InvalidManualTransition(DomainError):
    """A transition an operator may not request directly.

    Same code and status as any other rejected transition — to a client it is
    one — but the message says why, because a pair like RECEIVED -> ANALYSING
    is in the transition table and so looks legal from the outside.
    """

    code = "INVALID_TRANSITION"
    http_status = http_status.HTTP_409_CONFLICT

    def __init__(self, current: WorkItemStatus, target: WorkItemStatus) -> None:
        super().__init__(
            f"Cannot move work item from {current} to {target}."
            + (
                " That status is reached by running an analysis, not by setting it."
                if target in {WorkItemStatus.ANALYSING, WorkItemStatus.READY_FOR_REVIEW}
                else ""
            ),
            {"currentStatus": str(current), "requestedStatus": str(target)},
        )


def _base_queryset() -> QuerySet[WorkItem]:
    # No explicit order_by: `WorkItem.Meta.ordering` is newest-first with a
    # tiebreaker, and restating it here is how the two drift apart.
    return WorkItem.objects.all()


def _get_item_or_404(pk: Any) -> WorkItem:
    """Confirm the item exists before a view acts on it.

    The services raise `WorkItemNotFound` for a missing row too, so on the
    action endpoints this lookup is technically redundant. It is kept because
    it answers "does this exist?" before anything is constructed or claimed,
    which keeps 404 and 409 from depending on the order of steps inside a
    service. One indexed primary-key read on an operator-initiated action is
    not a cost worth optimising.
    """
    item = WorkItem.objects.filter(pk=pk).first()
    if item is None:
        raise WorkItemNotFound(pk)
    return item


def _serialize(item: WorkItem) -> dict:
    return WorkItemSerializer(item).data


@extend_schema(tags=["work-items"])
class WorkItemListCreateView(ListCreateAPIView):
    """`GET /work-items` (filter, paginate) and `POST /work-items` (idempotent intake)."""

    serializer_class = WorkItemSerializer

    def get_permissions(self):
        # Only intake is machine-to-machine; listing stays open for the demo UI.
        if self.request.method == "POST":
            return [HasIntakeApiKey()]
        return []

    def get_queryset(self) -> QuerySet[WorkItem]:
        queryset = _base_queryset()
        status_filter = self.request.query_params.get("status")

        if status_filter:
            valid = WorkItemStatus.values()
            if status_filter not in valid:
                raise InvalidStatusFilter(
                    f"{status_filter!r} is not a valid status.",
                    {"status": status_filter, "allowed": valid},
                )
            queryset = queryset.filter(status=status_filter)

        return queryset

    @extend_schema(
        summary="List work items, newest first",
        parameters=[
            OpenApiParameter("status", str, description="Filter by exact status."),
            OpenApiParameter("page", int),
            OpenApiParameter("pageSize", int, description="Max 100."),
        ],
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().get(request, *args, **kwargs)

    @extend_schema(
        summary="Submit a work item (idempotent)",
        request=WorkItemIntakeSerializer,
        responses={
            201: OpenApiResponse(WorkItemSerializer, "Created."),
            200: OpenApiResponse(WorkItemSerializer, "Replay of an identical submission."),
            409: OpenApiResponse(description="Same externalId, different content."),
        },
    )
    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = WorkItemIntakeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        result = create_or_get_work_item(**payload.validated_data)

        # 201 for a new item, 200 for a replay: the caller can tell whether it
        # caused the creation without us inventing a custom field.
        response = Response(
            _serialize(result.item),
            status=http_status.HTTP_201_CREATED if result.created else http_status.HTTP_200_OK,
        )
        if result.created:
            response["Location"] = f"/api/v1/work-items/{result.item.id}"
        return response


@extend_schema(tags=["work-items"], summary="One work item with its full history")
class WorkItemDetailView(RetrieveAPIView):
    serializer_class = WorkItemDetailSerializer

    def get_queryset(self) -> QuerySet[WorkItem]:
        # Prefetched so the history costs two extra queries, not 2N.
        return _base_queryset().prefetch_related(
            Prefetch("attempts", queryset=AnalysisAttempt.objects.order_by("-started_at")),
            Prefetch("transitions", queryset=StatusTransition.objects.order_by("-created_at")),
        )

    def get_object(self) -> WorkItem:
        item = self.get_queryset().filter(pk=self.kwargs["pk"]).first()
        if item is None:
            raise WorkItemNotFound(self.kwargs["pk"])
        return item


@extend_schema(
    tags=["work-items"],
    request=None,
    responses={
        200: OpenApiResponse(
            WorkItemSerializer,
            "Analysis ran. The item is READY_FOR_REVIEW or FAILED; check `status`.",
        ),
        409: OpenApiResponse(description="The item was not in the required state."),
    },
)
class AnalyseView(APIView):
    """`POST /work-items/{id}/analyse` — run the first analysis (from RECEIVED)."""

    @extend_schema(summary="Analyse a received work item")
    def post(self, request: Request, pk: Any) -> Response:
        _get_item_or_404(pk)
        item = analyse_work_item(pk)
        return Response(_serialize(item))


@extend_schema(
    tags=["work-items"],
    request=None,
    responses={
        200: OpenApiResponse(WorkItemSerializer, "Analysis re-ran."),
        409: OpenApiResponse(description="Not failed, or out of attempts."),
    },
)
class RetryView(APIView):
    """`POST /work-items/{id}/retry` — re-run an analysis that failed."""

    @extend_schema(summary="Retry a failed analysis")
    def post(self, request: Request, pk: Any) -> Response:
        _get_item_or_404(pk)
        item = retry_work_item(pk)
        return Response(_serialize(item))


@extend_schema(
    tags=["work-items"],
    request=StatusPatchSerializer,
    responses={
        200: OpenApiResponse(WorkItemSerializer, "Status changed."),
        409: OpenApiResponse(description="That transition is not allowed from here."),
    },
)
class StatusView(APIView):
    """`PATCH /work-items/{id}/status` — the operator's own decisions only."""

    @extend_schema(summary="Apply a manual status transition")
    def patch(self, request: Request, pk: Any) -> Response:
        payload = StatusPatchSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        target = WorkItemStatus(payload.validated_data["status"])

        item = _get_item_or_404(pk)
        current = item.domain_status

        # System-only targets (ANALYSING, READY_FOR_REVIEW) are refused here
        # even when the pair is in the transition table: reaching them means
        # actually running an analysis, and this endpoint has no side effects.
        if not is_manual_transition(current, target):
            raise InvalidManualTransition(current, target)

        updated = workflow.transition(
            pk,
            from_status=current,
            to_status=target,
            actor=TransitionActor.OPERATOR,
            reason="manual transition by operator",
        )
        return Response(_serialize(updated))
