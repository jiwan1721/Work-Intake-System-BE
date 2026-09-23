"""Real provider: Claude via tool calling (PLAN §7, decided 2026-09-22).

Structured output is requested properly rather than asked for in prose: the
tool's `input_schema` is generated from `AnalysisResult`, and `tool_choice`
forces the model to use it. That removes most of the "it wrote a paragraph
instead of JSON" failure mode.

It does not remove the need to validate. The orchestrator still runs
`parse_analysis` over whatever comes back, so a provider that returns a tool
call with a category we have never heard of fails exactly like a mock one.

This module is imported only when `AI_PROVIDER=anthropic`, so the default
install never needs the SDK and no test requires an API key.
"""

from __future__ import annotations

from typing import Any

import anthropic
from django.core.exceptions import ImproperlyConfigured

from .base import AIProviderError, AITimeoutError, WorkItemInput
from .prompt import SYSTEM_PROMPT, build_user_message
from .schema import AnalysisResult

DEFAULT_MODEL = "claude-sonnet-5"
TOOL_NAME = "record_analysis"
MAX_TOKENS = 1024


def analysis_tool_schema() -> dict[str, Any]:
    """The JSON Schema Claude must fill in, straight from the Pydantic model.

    `by_alias=True` gives camelCase (`recommendedAction`), matching both the
    API contract and what `parse_analysis` accepts.
    """
    return AnalysisResult.model_json_schema(by_alias=True)


class AnthropicProvider:
    """Implements the `AIProvider` protocol against the Anthropic Messages API."""

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.model = model or DEFAULT_MODEL

        if client is not None:
            self._client = client
            return

        if not api_key:
            raise ImproperlyConfigured(
                "AI_API_KEY is required when AI_PROVIDER=anthropic. "
                "Set it in .env (never commit it), or use AI_PROVIDER=mock."
            )
        # One SDK-level retry for transient errors; the workflow's own retry is
        # operator-driven and deliberately separate.
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=1)

    def analyse(self, item: WorkItemInput, *, timeout: float) -> str | dict:
        try:
            message = self._client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                # Triage should be reproducible, not creative.
                temperature=0,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_user_message(item)}],
                tools=[
                    {
                        "name": TOOL_NAME,
                        "description": "Record the triage analysis for one work item.",
                        "input_schema": analysis_tool_schema(),
                    }
                ],
                tool_choice={"type": "tool", "name": TOOL_NAME},
                timeout=timeout,
            )
        except anthropic.APITimeoutError as exc:
            raise AITimeoutError(f"Claude did not respond within {timeout:.0f}s.") from exc
        except anthropic.APIStatusError as exc:
            # 4xx and 5xx alike: from our side both mean "no usable answer".
            raise AIProviderError(f"Claude returned HTTP {exc.status_code}.") from exc
        except anthropic.APIError as exc:
            raise AIProviderError(f"Claude request failed: {exc}") from exc

        return _extract_tool_input(message)


def _extract_tool_input(message: Any) -> str | dict:
    """Pull the tool call out of the response.

    If the model answered with text instead of the tool, that text is returned
    unchanged so the parser can reject it and log what was actually said.
    """
    text_blocks: list[str] = []

    for block in getattr(message, "content", []) or []:
        block_type = getattr(block, "type", None)
        if block_type == "tool_use" and getattr(block, "name", None) == TOOL_NAME:
            return getattr(block, "input", {})
        if block_type == "text":
            text_blocks.append(getattr(block, "text", ""))

    if text_blocks:
        return "\n".join(text_blocks)

    raise AIProviderError("Claude returned a response with no usable content.")
