"""Shared abstract model mixins (adapted from BaseProject core/common/models/base.py)."""

from __future__ import annotations

from django.db import models
from django_currentuser.db.models import CurrentUserField


class CurrentUserModel(models.Model):
    created_by = CurrentUserField(
        related_name="%(app_label)s_%(class)s_created",
        on_delete=models.SET_NULL,
        null=True,
    )
    modified_by = CurrentUserField(
        related_name="%(app_label)s_%(class)s_modified",
        on_delete=models.SET_NULL,
        null=True,
        on_update=True,
    )

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-modified_at")
        abstract = True


class BaseModel(CurrentUserModel, TimeStampedModel):
    """Combine audit user tracking with timestamps. Extend this in new models."""

    class Meta:
        abstract = True
