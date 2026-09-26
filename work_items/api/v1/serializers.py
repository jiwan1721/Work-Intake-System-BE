"""The v1 wire contract (PLAN §8).

JSON is camelCase because that is what the external system already speaks
(`externalId`); Python stays snake_case. The mapping is explicit here, which
is the point of versioning only this layer: a v2 with different field names
gets its own serializers and calls the same services.

`allowedActions` is computed by the domain rather than hard-coded, so the
buttons the frontend renders and the transitions the backend accepts can never
drift apart.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from rest_framework import serializers

from common.mixins.serializers import DynamicFieldsModelSerializer, DynamicFieldsSerializerMixin
from ...domain.status import WorkItemStatus
from ...models import AnalysisAttempt, StatusTransition, WorkItem

#: PLAN §4: descriptions are long free text, but not unbounded.
MAX_DESCRIPTION_CHARS = 10_000


class WorkItemIntakeSerializer(DynamicFieldsSerializerMixin, serializers.Serializer):
    """What the external system may post."""

    externalId = serializers.CharField(
        source="external_id", max_length=100, allow_blank=False, trim_whitespace=True
    )
    title = serializers.CharField(max_length=200, allow_blank=False, trim_whitespace=True)
    description = serializers.CharField(
        max_length=MAX_DESCRIPTION_CHARS, allow_blank=False, trim_whitespace=True
    )


class StatusPatchSerializer(DynamicFieldsSerializerMixin, serializers.Serializer):
    """What an operator may request through PATCH /status.

    Only the target status: a status change expresses a human decision and
    must never carry analysis fields with it.
    """

    status = serializers.ChoiceField(choices=WorkItemStatus.values())


class AnalysisAttemptSerializer(DynamicFieldsModelSerializer):
    attemptNo = serializers.IntegerField(source="attempt_no", read_only=True)
    errorCode = serializers.CharField(source="error_code", read_only=True)
    errorMessage = serializers.CharField(source="error_message", read_only=True)
    latencyMs = serializers.IntegerField(source="latency_ms", read_only=True)
    promptVersion = serializers.CharField(source="prompt_version", read_only=True)
    startedAt = serializers.DateTimeField(source="started_at", read_only=True)
    finishedAt = serializers.DateTimeField(source="finished_at", read_only=True)

    class Meta:
        model = AnalysisAttempt
        fields = (
            "attemptNo",
            "outcome",
            "provider",
            "model",
            "promptVersion",
            "errorCode",
            "errorMessage",
            "latencyMs",
            "startedAt",
            "finishedAt",
        )
        # `raw_output` is deliberately not exposed: it is debugging material,
        # and showing unvalidated model text in the UI is how it ends up being
        # treated as a result.


class StatusTransitionSerializer(DynamicFieldsModelSerializer):
    fromStatus = serializers.CharField(source="from_status", read_only=True)
    toStatus = serializers.CharField(source="to_status", read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = StatusTransition
        fields = ("fromStatus", "toStatus", "actor", "reason", "createdAt")


class WorkItemSerializer(DynamicFieldsModelSerializer):
    """The item shape from PLAN §8. Used for list responses and intake."""

    externalId = serializers.CharField(source="external_id", read_only=True)
    attemptCount = serializers.IntegerField(source="attempt_count", read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="modified_at", read_only=True)

    analysis = serializers.SerializerMethodField()
    lastError = serializers.SerializerMethodField()
    allowedActions = serializers.SerializerMethodField()

    class Meta:
        model = WorkItem
        fields = (
            "id",
            "externalId",
            "external_id",
            "title",
            "description",
            "status",
            "analysis",
            "lastError",
            "attemptCount",
            "allowedActions",
            "version",
            "createdAt",
            "updatedAt",
        )

    def get_analysis(self, obj: WorkItem) -> dict[str, Any] | None:
        """Null until a valid result exists, never a half-filled object."""
        if not obj.has_analysis:
            return None
        return {
            "category": obj.category,
            "priority": obj.priority,
            "summary": obj.summary,
            "recommendedAction": obj.recommended_action,
            "analysedAt": obj.analysed_at,
            "model": obj.analysis_model,
        }

    def get_lastError(self, obj: WorkItem) -> dict[str, Any] | None:
        if not obj.last_error_code:
            return None
        return {"code": obj.last_error_code, "message": obj.last_error_message}

    def get_allowedActions(self, obj: WorkItem) -> list[str]:
        return obj.allowed_actions(max_attempts=settings.AI_MAX_ATTEMPTS)


class WorkItemDetailSerializer(WorkItemSerializer):
    """The item, plus its history. Only the detail endpoint pays for these."""

    attempts = AnalysisAttemptSerializer(many=True, read_only=True)
    transitions = StatusTransitionSerializer(many=True, read_only=True)

    class Meta(WorkItemSerializer.Meta):
        fields = (*WorkItemSerializer.Meta.fields, "attempts", "transitions")
