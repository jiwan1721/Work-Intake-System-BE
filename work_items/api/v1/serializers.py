"""The v1 wire contract (PLAN §8).

Field names are now snake_case end-to-end (API ↔ Python ↔ DB). The explicit
camelCase aliases have been removed; `updated_at` is the only remaining
explicit declaration because the underlying column is `modified_at`.
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

    external_id = serializers.CharField(
        max_length=100, allow_blank=False, trim_whitespace=True
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
    class Meta:
        model = AnalysisAttempt
        fields = (
            "attempt_no",
            "outcome",
            "provider",
            "model",
            "prompt_version",
            "error_code",
            "error_message",
            "latency_ms",
            "started_at",
            "finished_at",
        )
        # `raw_output` is deliberately not exposed: it is debugging material,
        # and showing unvalidated model text in the UI is how it ends up being
        # treated as a result.


class StatusTransitionSerializer(DynamicFieldsModelSerializer):
    class Meta:
        model = StatusTransition
        fields = ("from_status", "to_status", "actor", "reason", "created_at")


class WorkItemSerializer(DynamicFieldsModelSerializer):
    """The item shape from PLAN §8. Used for list responses and intake."""

    # `modified_at` is the DB column name (from BaseModel); expose it as
    # `updated_at` to keep the external-facing field name stable.
    updated_at = serializers.DateTimeField(source="modified_at", read_only=True)

    analysis = serializers.SerializerMethodField()
    last_error = serializers.SerializerMethodField()
    allowed_actions = serializers.SerializerMethodField()

    class Meta:
        model = WorkItem
        fields = (
            "id",
            "external_id",
            "title",
            "description",
            "status",
            "analysis",
            "last_error",
            "attempt_count",
            "allowed_actions",
            "version",
            "created_at",
            "updated_at",
        )

    def get_analysis(self, obj: WorkItem) -> dict[str, Any] | None:
        """Null until a valid result exists, never a half-filled object."""
        if not obj.has_analysis:
            return None
        return {
            "category": obj.category,
            "priority": obj.priority,
            "summary": obj.summary,
            "recommended_action": obj.recommended_action,
            "analysed_at": obj.analysed_at,
            "model": obj.analysis_model,
        }

    def get_last_error(self, obj: WorkItem) -> dict[str, Any] | None:
        if not obj.last_error_code:
            return None
        return {"code": obj.last_error_code, "message": obj.last_error_message}

    def get_allowed_actions(self, obj: WorkItem) -> list[str]:
        return obj.allowed_actions(max_attempts=settings.AI_MAX_ATTEMPTS)


class WorkItemDetailSerializer(WorkItemSerializer):
    """The item, plus its history. Only the detail endpoint pays for these."""

    attempts = AnalysisAttemptSerializer(many=True, read_only=True)
    transitions = StatusTransitionSerializer(many=True, read_only=True)

    class Meta(WorkItemSerializer.Meta):
        fields = (*WorkItemSerializer.Meta.fields, "attempts", "transitions")
