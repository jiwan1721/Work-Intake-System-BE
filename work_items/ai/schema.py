"""The shape a model is allowed to return (PLAN §7).

Model output is untrusted input. It arrives as text from a system that is
probabilistic by construction, so it gets the same treatment as a request body
from the internet: parse, validate against a strict schema, reject anything
that does not fit.

The deliberate choice here is to **reject** unknown enum values rather than
map them to OTHER. Silent coercion hides a model that has started
misbehaving; a visible FAILED item with a retry button does not. In a
regulated domain, a wrong-but-plausible category is worse than a missing one.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Category(StrEnum):
    DOCUMENT_REQUEST = "DOCUMENT_REQUEST"
    INFORMATION_REQUEST = "INFORMATION_REQUEST"
    COMPLAINT = "COMPLAINT"
    TECHNICAL_ISSUE = "TECHNICAL_ISSUE"
    ACCOUNT_CHANGE = "ACCOUNT_CHANGE"
    OTHER = "OTHER"


class Priority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


MAX_TEXT_LENGTH = 500


class AnalysisResult(BaseModel):
    """A validated analysis. Nothing reaches a WorkItem without passing this."""

    model_config = ConfigDict(
        extra="ignore",  # a model that adds "confidence" should not fail the call
        str_strip_whitespace=True,
        populate_by_name=True,  # accept recommended_action as well as the alias
        frozen=True,
    )

    category: Category
    priority: Priority
    summary: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    recommended_action: str = Field(
        alias="recommendedAction", min_length=1, max_length=MAX_TEXT_LENGTH
    )

    @field_validator("category", "priority", mode="before")
    @classmethod
    def normalise_enum_case(cls, value: Any) -> Any:
        """Accept "high" and " High " as HIGH.

        Case and padding are noise, not meaning. An unrecognised *value* still
        fails: this normalises presentation, it does not invent members.
        """
        if isinstance(value, str):
            return value.strip().upper().replace("-", "_").replace(" ", "_")
        return value
