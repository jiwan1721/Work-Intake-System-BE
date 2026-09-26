"""Admin registrations, for demoing and debugging the data model.

Read-only where it matters: status changes must go through the workflow
service (PLAN §5), never through an admin form.
"""

from __future__ import annotations

from django.contrib import admin

from .models import AnalysisAttempt, StatusTransition, WorkItem


class AnalysisAttemptInline(admin.TabularInline):
    model = AnalysisAttempt
    extra = 0
    can_delete = False
    fields = (
        "attempt_no",
        "outcome",
        "provider",
        "model",
        "error_code",
        "latency_ms",
        "started_at",
    )
    readonly_fields = fields
    ordering = ("-started_at",)

    def has_add_permission(self, request, obj=None) -> bool:
        return False


class StatusTransitionInline(admin.TabularInline):
    model = StatusTransition
    extra = 0
    can_delete = False
    fields = ("from_status", "to_status", "actor", "reason", "created_at")
    readonly_fields = fields
    ordering = ("-created_at",)

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(WorkItem)
class WorkItemAdmin(admin.ModelAdmin):
    list_display = (
        "external_id",
        "title",
        "status",
        "category",
        "priority",
        "attempt_count",
        "created_at",
    )
    list_filter = ("status", "category", "priority")
    search_fields = ("external_id", "title", "description")
    date_hierarchy = "created_at"
    # No `ordering`: the model's Meta ordering is newest-first and total, which
    # is what the admin's own pagination needs.
    inlines = (AnalysisAttemptInline, StatusTransitionInline)
    # Status is owned by workflow.transition(); editing it here would bypass
    # the state machine and the audit trail.
    readonly_fields = (
        "id",
        "status",
        "content_hash",
        "version",
        "attempt_count",
        "analysed_at",
        "analysis_started_at",
        "created_at",
        "modified_at",
        "completed_at",
    )


@admin.register(AnalysisAttempt)
class AnalysisAttemptAdmin(admin.ModelAdmin):
    list_display = ("work_item", "attempt_no", "outcome", "provider", "model", "error_code")
    list_filter = ("outcome", "provider", "error_code")
    search_fields = ("work_item__external_id",)
    ordering = ("-started_at",)


@admin.register(StatusTransition)
class StatusTransitionAdmin(admin.ModelAdmin):
    list_display = ("work_item", "from_status", "to_status", "actor", "created_at")
    list_filter = ("actor", "to_status")
    search_fields = ("work_item__external_id",)
    ordering = ("-created_at",)
