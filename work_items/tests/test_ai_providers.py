"""BE-06 and BE-08: the mock provider, the factory, and the Claude adapter.

No network and no API key: the Anthropic tests drive a stub client object.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from work_items.ai.base import (
    AIInvalidOutputError,
    AIProvider,
    AIProviderError,
    AITimeoutError,
    WorkItemInput,
)
from work_items.ai.factory import ALLOWED_PROVIDERS, get_ai_provider
from work_items.ai.mock_provider import (
    FAILURE_BAD_ENUM,
    FAILURE_ERROR,
    FAILURE_MALFORMED,
    FAILURE_TIMEOUT,
    MOCK_MODEL,
    MockProvider,
)
from work_items.ai.parser import parse_analysis
from work_items.ai.schema import Category, Priority

PAYSLIP = WorkItemInput(
    external_id="CRM-12345",
    title="Missing income document",
    description="The applicant submitted their application but no payslip was attached.",
)


def analyse(provider: Any, item: WorkItemInput = PAYSLIP) -> Any:
    return provider.analyse(item, timeout=5.0)


# --- MockProvider ----------------------------------------------------------


def test_mock_provider_satisfies_the_protocol() -> None:
    assert isinstance(MockProvider(), AIProvider)
    assert MockProvider().name == "mock"
    assert MockProvider().model == MOCK_MODEL


def test_the_payslip_example_is_a_high_priority_document_request() -> None:
    result = parse_analysis(analyse(MockProvider()))

    assert result.category is Category.DOCUMENT_REQUEST
    assert result.priority is Priority.HIGH
    assert result.summary
    assert result.recommended_action


def test_classification_is_deterministic() -> None:
    provider = MockProvider()
    assert analyse(provider) == analyse(provider)


@pytest.mark.parametrize(
    ("title", "description", "category"),
    [
        ("Missing payslip", "Please upload your latest payslip.", Category.DOCUMENT_REQUEST),
        ("Unhappy with the delay", "This is unacceptable, I want to complain.", Category.COMPLAINT),
        ("Cannot log in", "I get an error every time I try.", Category.TECHNICAL_ISSUE),
        ("Moving house", "I need to change my address on file.", Category.ACCOUNT_CHANGE),
        ("Quick question", "How long will my application take?", Category.INFORMATION_REQUEST),
        ("Mystery", "Nothing in particular.", Category.OTHER),
    ],
)
def test_keyword_rules(title: str, description: str, category: Category) -> None:
    result = parse_analysis(analyse(MockProvider(), WorkItemInput("CRM-1", title, description)))
    assert result.category is category


def test_urgency_words_escalate_the_priority() -> None:
    normal = parse_analysis(
        analyse(MockProvider(), WorkItemInput("CRM-1", "Quick question", "How long will it take?"))
    )
    urgent = parse_analysis(
        analyse(
            MockProvider(),
            WorkItemInput("CRM-2", "Quick question", "How long will it take? This is urgent."),
        )
    )

    assert normal.priority is Priority.LOW
    assert urgent.priority is Priority.MEDIUM


@pytest.mark.parametrize(
    ("marker", "expectation"),
    [
        (FAILURE_TIMEOUT, "timeout"),
        (FAILURE_MALFORMED, "malformed"),
        (FAILURE_BAD_ENUM, "bad_enum"),
        (FAILURE_ERROR, "error"),
    ],
)
def test_simulate_markers_in_the_title(marker: str, expectation: str) -> None:
    item = WorkItemInput("CRM-1", f"[simulate:{marker}] Missing payslip", "Please upload it.")
    provider = MockProvider()

    if expectation == "timeout":
        with pytest.raises(AITimeoutError):
            analyse(provider, item)
    elif expectation == "error":
        with pytest.raises(AIProviderError):
            analyse(provider, item)
    else:
        raw = analyse(provider, item)
        with pytest.raises(AIInvalidOutputError):
            parse_analysis(raw)


def test_bad_enum_marker_returns_json_with_an_unknown_category() -> None:
    item = WorkItemInput("CRM-1", "[simulate:bad_enum] Anything", "Anything.")

    raw = analyse(MockProvider(), item)

    assert json.loads(raw)["category"] == "SPAM"


def test_malformed_marker_returns_text_that_is_not_json() -> None:
    item = WorkItemInput("CRM-1", "[simulate:malformed] Anything", "Anything.")

    raw = analyse(MockProvider(), item)

    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)


def test_global_failure_mode_applies_to_every_item() -> None:
    provider = MockProvider(failure_mode=FAILURE_ERROR)

    with pytest.raises(AIProviderError):
        analyse(provider)


def test_a_marker_beats_the_global_failure_mode() -> None:
    provider = MockProvider(failure_mode=FAILURE_ERROR)
    item = WorkItemInput("CRM-1", "[simulate:timeout] Anything", "Anything.")

    with pytest.raises(AITimeoutError):
        analyse(provider, item)


def test_an_unknown_global_failure_mode_is_ignored_rather_than_crashing() -> None:
    provider = MockProvider(failure_mode="nonsense")

    assert parse_analysis(analyse(provider)).category is Category.DOCUMENT_REQUEST


def test_the_simulate_marker_is_not_treated_as_part_of_the_title() -> None:
    item = WorkItemInput("CRM-1", "[simulate:none] Missing payslip", "Please upload it.")

    result = parse_analysis(analyse(MockProvider(), item))

    assert "[simulate:none]" not in result.summary


def test_timeout_simulation_never_sleeps() -> None:
    # A one-hour latency would hang the suite if the timeout path slept first.
    provider = MockProvider(latency_ms=3_600_000, failure_mode=FAILURE_TIMEOUT)

    with pytest.raises(AITimeoutError):
        analyse(provider)


# --- Factory ---------------------------------------------------------------


@override_settings(AI_PROVIDER="mock", MOCK_AI_LATENCY_MS=0, MOCK_AI_FAILURE_MODE="none")
def test_factory_returns_the_mock_provider_by_default() -> None:
    provider = get_ai_provider()

    assert isinstance(provider, MockProvider)
    assert provider.latency_ms == 0


@override_settings(AI_PROVIDER="mock", MOCK_AI_LATENCY_MS=250, MOCK_AI_FAILURE_MODE="malformed")
def test_factory_passes_settings_to_the_mock_provider() -> None:
    provider = get_ai_provider()

    assert provider.latency_ms == 250
    assert provider.failure_mode == "malformed"


@override_settings(AI_PROVIDER="mock")
def test_factory_accepts_an_explicit_name() -> None:
    assert isinstance(get_ai_provider("MOCK"), MockProvider)


@override_settings(AI_PROVIDER="telepathy")
def test_unknown_provider_lists_the_allowed_values() -> None:
    with pytest.raises(ImproperlyConfigured) as exc_info:
        get_ai_provider()

    message = str(exc_info.value)
    assert "telepathy" in message
    for allowed in ALLOWED_PROVIDERS:
        assert allowed in message


@override_settings(AI_PROVIDER="anthropic", AI_API_KEY="", AI_MODEL="")
def test_anthropic_without_a_key_fails_loudly() -> None:
    with pytest.raises(ImproperlyConfigured, match="AI_API_KEY"):
        get_ai_provider()
