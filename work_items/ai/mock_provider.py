"""A deterministic stand-in for a real model (PLAN §7).

This is the default provider, and the reason the whole system runs, demos and
tests without an API key. Two things make it useful rather than a stub:

1. Its classifications are deterministic keyword rules, so the UI shows
   plausible data and tests can assert on exact values.
2. It can fail on demand. Every failure path the orchestrator handles —
   timeout, malformed text, an out-of-enum value, a provider error — is
   reachable either per item (a `[simulate:...]` marker in the title, which is
   what the seed command uses) or globally (`MOCK_AI_FAILURE_MODE`).
"""

from __future__ import annotations

import json
import re
import time
from typing import Final

from .base import AIProviderError, AITimeoutError, WorkItemInput
from .schema import Category, Priority

MOCK_MODEL: Final = "mock-v1"

FAILURE_NONE: Final = "none"
FAILURE_TIMEOUT: Final = "timeout"
FAILURE_MALFORMED: Final = "malformed"
FAILURE_BAD_ENUM: Final = "bad_enum"
FAILURE_ERROR: Final = "error"

FAILURE_MODES: Final = frozenset(
    {FAILURE_NONE, FAILURE_TIMEOUT, FAILURE_MALFORMED, FAILURE_BAD_ENUM, FAILURE_ERROR}
)

_MARKER_RE = re.compile(r"\[simulate:\s*([a-z_]+)\s*\]", re.IGNORECASE)

# Ordered: the first rule whose keywords appear wins, so a complaint about a
# missing document is triaged as a complaint.
_RULES: Final[tuple[tuple[Category, Priority, tuple[str, ...]], ...]] = (
    (
        Category.COMPLAINT,
        Priority.URGENT,
        ("complaint", "complain", "unhappy", "dissatisfied", "unacceptable", "escalate"),
    ),
    (
        Category.TECHNICAL_ISSUE,
        Priority.HIGH,
        ("error", "bug", "crash", "broken", "cannot log in", "can't log in", "outage", "failing"),
    ),
    (
        Category.DOCUMENT_REQUEST,
        Priority.HIGH,
        ("document", "payslip", "statement", "proof of", "upload", "attach", "id copy", "passport"),
    ),
    (
        Category.ACCOUNT_CHANGE,
        Priority.MEDIUM,
        ("change of address", "update my", "change my", "close my account", "new bank details"),
    ),
    (
        Category.INFORMATION_REQUEST,
        Priority.LOW,
        ("how do i", "how long", "when will", "could you confirm", "question", "clarify", "status"),
    ),
)

# Words that raise the priority by one step regardless of category.
_ESCALATORS: Final = ("urgent", "asap", "immediately", "deadline", "today", "regulator")

_PRIORITY_LADDER: Final = (Priority.LOW, Priority.MEDIUM, Priority.HIGH, Priority.URGENT)


class MockProvider:
    """Implements the `AIProvider` protocol without leaving the process."""

    name = "mock"

    def __init__(
        self,
        *,
        model: str = MOCK_MODEL,
        latency_ms: int = 0,
        failure_mode: str = FAILURE_NONE,
    ) -> None:
        self.model = model
        self.latency_ms = max(latency_ms, 0)
        self.failure_mode = failure_mode if failure_mode in FAILURE_MODES else FAILURE_NONE

    def analyse(self, item: WorkItemInput, *, timeout: float) -> str | dict:
        mode = self._mode_for(item)

        if mode == FAILURE_TIMEOUT:
            # Raise straight away rather than actually sleeping: a test suite
            # must not have to wait out a simulated timeout.
            raise AITimeoutError(f"Mock provider timed out after {timeout:.0f}s.")

        self._simulate_latency()

        if mode == FAILURE_ERROR:
            raise AIProviderError("Mock provider is unavailable (simulated 503).")
        if mode == FAILURE_MALFORMED:
            return "Sure! Here's the triage: the item looks like a document request."
        if mode == FAILURE_BAD_ENUM:
            return json.dumps(
                {
                    "category": "SPAM",
                    "priority": "HIGH",
                    "summary": "Looks like spam to me.",
                    "recommendedAction": "Discard the item.",
                }
            )

        return json.dumps(self._classify(item))

    # --- internals ---------------------------------------------------------

    def _mode_for(self, item: WorkItemInput) -> str:
        """A per-item marker beats the global setting."""
        match = _MARKER_RE.search(item.title)
        if match:
            candidate = match.group(1).lower()
            if candidate in FAILURE_MODES:
                return candidate
        return self.failure_mode

    def _simulate_latency(self) -> None:
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000)

    def _classify(self, item: WorkItemInput) -> dict[str, str]:
        haystack = f"{_MARKER_RE.sub('', item.title)} {item.description}".lower()

        category, priority = Category.OTHER, Priority.MEDIUM
        for rule_category, rule_priority, keywords in _RULES:
            if any(keyword in haystack for keyword in keywords):
                category, priority = rule_category, rule_priority
                break

        if any(word in haystack for word in _ESCALATORS):
            priority = _escalate(priority)

        return {
            "category": category.value,
            "priority": priority.value,
            "summary": _summarise(category, item),
            "recommendedAction": _recommend(category),
        }


def _escalate(priority: Priority) -> Priority:
    index = _PRIORITY_LADDER.index(priority)
    return _PRIORITY_LADDER[min(index + 1, len(_PRIORITY_LADDER) - 1)]


def _summarise(category: Category, item: WorkItemInput) -> str:
    subject = _MARKER_RE.sub("", item.title).strip() or item.external_id
    lead = {
        Category.DOCUMENT_REQUEST: "A document is missing or needs to be resupplied",
        Category.INFORMATION_REQUEST: "The customer is asking for information",
        Category.COMPLAINT: "The customer is unhappy and expects a response",
        Category.TECHNICAL_ISSUE: "Something in the product is not working",
        Category.ACCOUNT_CHANGE: "The customer wants their account details changed",
        Category.OTHER: "The item does not fit the standard categories",
    }[category]
    return f"{lead}: {subject}."[:500]


def _recommend(category: Category) -> str:
    return {
        Category.DOCUMENT_REQUEST: "Request the missing document from the applicant.",
        Category.INFORMATION_REQUEST: "Reply to the customer with the requested information.",
        Category.COMPLAINT: "Acknowledge the complaint and route it to the complaints team.",
        Category.TECHNICAL_ISSUE: "Raise a defect with engineering and tell the customer the ETA.",
        Category.ACCOUNT_CHANGE: "Verify the customer's identity, then apply the change.",
        Category.OTHER: "Review the item manually and assign it to the right team.",
    }[category]
