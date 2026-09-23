"""The provider seam and its error vocabulary (PLAN §7).

Everything the rest of the system knows about an LLM is in this file: it takes
a plain dataclass and returns text or a dict, or it raises one of four typed
errors. Swapping Anthropic for another vendor touches only `ai/`.

`AIError.code` is what ends up in `WorkItem.last_error_code` and in the API
error envelope, so the failure an operator sees is the failure that happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class WorkItemInput:
    """What a provider is allowed to see. Deliberately not a Django model."""

    external_id: str
    title: str
    description: str


class AIError(Exception):
    """Base class for every expected failure of an analysis."""

    code = "UNKNOWN"

    def __init__(self, message: str, *, raw_output: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        # Kept on the attempt row for debugging; never shown as a result.
        self.raw_output = raw_output


class AITimeoutError(AIError):
    """The model did not answer inside AI_TIMEOUT_SECONDS."""

    code = "TIMEOUT"


class AIProviderError(AIError):
    """Network failure, 5xx, rate limit, or bad credentials."""

    code = "PROVIDER_ERROR"


class AIInvalidOutputError(AIError):
    """The answer was not JSON, or did not match `AnalysisResult`."""

    code = "INVALID_OUTPUT"


#: Errors that indicate a bug rather than a known failure mode still have to
#: leave the item in a sane state, so the orchestrator maps them to this code.
UNEXPECTED_ERROR_CODE = "UNEXPECTED_ERROR"

#: Set by the reaper on analyses whose process died mid-call.
STALE_ANALYSIS_CODE = "STALE_ANALYSIS"


@runtime_checkable
class AIProvider(Protocol):
    """What every adapter must offer.

    Returning `str | dict` rather than an `AnalysisResult` is intentional: a
    provider reports what the model said, and validation happens in exactly
    one place (`parser.parse_analysis`) for every provider, including the ones
    that claim to return structured output.
    """

    name: str
    model: str

    def analyse(self, item: WorkItemInput, *, timeout: float) -> str | dict: ...
