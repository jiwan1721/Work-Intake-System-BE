"""Custom User model (adapted from BaseProject core/users/models/models.py)."""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

from common.models.validators import validate_names_only
from users.manager import UserManager


class User(AbstractUser):
    email = models.EmailField(_("email address"), unique=True)
    is_blocked = models.BooleanField(
        default=False,
        help_text="Blocked users cannot log in even with valid credentials.",
    )
    # Tokens issued before this datetime are rejected. Updated on every
    # password change so a compromised token stops working immediately.
    token_refresh_date = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    # username is auto-populated from email; first/last name required on register
    REQUIRED_FIELDS = ["first_name", "last_name"]

    objects = UserManager()

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")

    def set_password(self, raw_password):
        super().set_password(raw_password)
        # Mark the moment so any token issued before now becomes invalid.
        self.token_refresh_date = now()

    def save(self, *args, **kwargs):
        # Keep username in sync with email so Django's admin and auth machinery
        # (which defaults to username) continues to work without extra config.
        if not self.username:
            self.username = self.email
        self.email = self.email.lower()
        super().save(*args, **kwargs)

    @property
    def full_name(self) -> str:
        parts = [p for p in [self.first_name, self.last_name] if p]
        return " ".join(parts)

    def block(self) -> None:
        self.is_blocked = True
        self.is_active = False
        self.save(update_fields=["is_blocked", "is_active"])

    def __str__(self) -> str:
        return self.full_name or self.email
