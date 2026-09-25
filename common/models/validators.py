from __future__ import annotations

import re

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError


def validate_names_only(value: str) -> None:
    if re.fullmatch(r"[a-zA-Z0-9 \.\,\(\)]+", value):
        return
    raise ValidationError(
        _("The field must contain alphabets, numbers, brackets, commas and dots only.")
    )
