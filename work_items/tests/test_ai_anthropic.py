"""BE-08: the Claude adapter, driven by a stub client.

No network, no API key. These tests care about the adapter's contract:
what it sends, what it returns, and how SDK failures are translated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anthropic
import httpx
import pytest

from work_items.ai.anthropic_provider import (
    TOOL_NAME,
    AnthropicProvider,
    analysis_tool_schema,
)
from work_items.ai.base import AIProviderError, AITimeoutError, WorkItemInput
from work_items.ai.parser import parse_analysis
from work_items.ai.schema import Category, Priority

ITEM = WorkItemInput(
    external_id="CRM-12345",
    title="Missing income document",
    description="No payslip was attached.",
)

TOOL_INPUT = {
    "category": "DOCUMENT_REQUEST",
    "priority": "HIGH",
    "summary": "The applicant needs to provide their latest payslip.",
    "recommendedAction": "Request the missing payslip from the applicant.",
}


@dataclass
class StubBlock:
    type: str
    name: str = ""
    input: Any = None
    text: str = ""


class StubMessages:
    def __init__(self, *, response: Any = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


class StubClient:
    def __init__(self, *, response: Any = None, error: Exception | None = None) -> None:
        self.messages = StubMessages(response=response, error=error)


@dataclass
class StubResponse:
    content: list[StubBlock]


def provider_returning(blocks: list[StubBlock]) -> tuple[AnthropicProvider, StubClient]:
    client = StubClient(response=StubResponse(content=blocks))
    return AnthropicProvider(client=client, model="claude-sonnet-5"), client


def provider_raising(error: Exception) -> AnthropicProvider:
    return AnthropicProvider(client=StubClient(error=error), model="claude-sonnet-5")


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


# --- Tool schema -----------------------------------------------------------


def test_tool_schema_comes_from_the_pydantic_model_in_camel_case() -> None:
    schema = analysis_tool_schema()

    assert schema["type"] == "object"
    assert set(schema["required"]) == {"category", "priority", "summary", "recommendedAction"}


def test_tool_schema_constrains_the_enums() -> None:
    # The model is told which values exist, rather than being asked in prose.
    rendered = str(analysis_tool_schema())
    for category in Category:
        assert category.value in rendered
    for priority in Priority:
        assert priority.value in rendered


# --- Request ---------------------------------------------------------------


def test_the_request_forces_the_tool_and_pins_the_settings() -> None:
    provider, client = provider_returning(
        [StubBlock(type="tool_use", name=TOOL_NAME, input=TOOL_INPUT)]
    )

    provider.analyse(ITEM, timeout=12.5)

    sent = client.messages.calls[0]
    assert sent["model"] == "claude-sonnet-5"
    assert sent["temperature"] == 0
    assert sent["timeout"] == 12.5
    assert sent["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert sent["tools"][0]["name"] == TOOL_NAME
    assert sent["tools"][0]["input_schema"] == analysis_tool_schema()


def test_the_item_text_is_sent_inside_the_delimiters() -> None:
    provider, client = provider_returning(
        [StubBlock(type="tool_use", name=TOOL_NAME, input=TOOL_INPUT)]
    )

    provider.analyse(ITEM, timeout=5)

    content = client.messages.calls[0]["messages"][0]["content"]
    assert "<work_item>" in content
    assert ITEM.description in content


# --- Response --------------------------------------------------------------


def test_a_tool_call_is_returned_and_validates() -> None:
    provider, _ = provider_returning([StubBlock(type="tool_use", name=TOOL_NAME, input=TOOL_INPUT)])

    result = parse_analysis(provider.analyse(ITEM, timeout=5))

    assert result.category is Category.DOCUMENT_REQUEST
    assert result.priority is Priority.HIGH


def test_a_tool_call_with_a_bad_enum_still_fails_validation() -> None:
    """Structured output is not a substitute for validating it."""
    provider, _ = provider_returning(
        [StubBlock(type="tool_use", name=TOOL_NAME, input={**TOOL_INPUT, "category": "SPAM"})]
    )

    from work_items.ai.base import AIInvalidOutputError

    with pytest.raises(AIInvalidOutputError):
        parse_analysis(provider.analyse(ITEM, timeout=5))


def test_a_text_answer_is_passed_through_so_the_parser_can_reject_it() -> None:
    provider, _ = provider_returning([StubBlock(type="text", text="I'd rather not.")])

    raw = provider.analyse(ITEM, timeout=5)

    assert raw == "I'd rather not."


def test_an_empty_response_is_a_provider_error() -> None:
    provider, _ = provider_returning([])

    with pytest.raises(AIProviderError):
        provider.analyse(ITEM, timeout=5)


# --- Error mapping ---------------------------------------------------------


def test_sdk_timeout_maps_to_ai_timeout_error() -> None:
    provider = provider_raising(anthropic.APITimeoutError(request=_request()))

    with pytest.raises(AITimeoutError) as exc_info:
        provider.analyse(ITEM, timeout=5)

    assert exc_info.value.code == "TIMEOUT"


def test_sdk_status_error_maps_to_provider_error() -> None:
    provider = provider_raising(
        anthropic.RateLimitError(
            "rate limited",
            response=httpx.Response(429, request=_request()),
            body=None,
        )
    )

    with pytest.raises(AIProviderError) as exc_info:
        provider.analyse(ITEM, timeout=5)

    assert exc_info.value.code == "PROVIDER_ERROR"
    assert "429" in exc_info.value.message


def test_sdk_connection_error_maps_to_provider_error() -> None:
    provider = provider_raising(anthropic.APIConnectionError(request=_request()))

    with pytest.raises(AIProviderError):
        provider.analyse(ITEM, timeout=5)


def test_the_api_key_is_never_put_in_an_error_message() -> None:
    provider = AnthropicProvider(api_key="sk-ant-secret-value", model="claude-sonnet-5")
    provider._client = StubClient(error=anthropic.APIConnectionError(request=_request()))

    with pytest.raises(AIProviderError) as exc_info:
        provider.analyse(ITEM, timeout=5)

    assert "sk-ant-secret-value" not in str(exc_info.value)
