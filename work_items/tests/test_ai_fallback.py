"""Tests for the FallbackProvider wrapper and its factory integration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from django.test import override_settings

from work_items.ai.base import (
    AIInvalidOutputError,
    AIProviderError,
    AITimeoutError,
    WorkItemInput,
)
from work_items.ai.fallback_provider import FallbackProvider
from work_items.ai.factory import get_ai_provider
from work_items.ai.mock_provider import MockProvider

ITEM = WorkItemInput(
    external_id="FB-1",
    title="Missing payslip",
    description="No payslip was attached.",
)


def _primary(raises: Exception) -> MagicMock:
    p = MagicMock()
    p.name = "fake"
    p.model = "fake-model"
    p.analyse.side_effect = raises
    return p


# --- Protocol compliance -----------------------------------------------------


def test_fallback_provider_satisfies_protocol() -> None:
    primary = _primary(raises=AIProviderError("down"))
    fp = FallbackProvider(primary)
    assert fp.name == "fake+mock"
    assert fp.model == "fake-model"


# --- Happy path: primary succeeds, mock never called -------------------------


def test_primary_success_returned_directly() -> None:
    primary = MagicMock()
    primary.name = "fake"
    primary.model = "fake-model"
    primary.analyse.return_value = {"category": "OTHER", "priority": "MEDIUM",
                                    "summary": "x", "recommendedAction": "y"}
    fp = FallbackProvider(primary)
    result = fp.analyse(ITEM, timeout=5.0)

    assert result == primary.analyse.return_value
    primary.analyse.assert_called_once()


# --- Fallback on AIError subclasses ------------------------------------------


@pytest.mark.parametrize("exc", [
    AITimeoutError("timed out"),
    AIProviderError("503"),
    AIInvalidOutputError("bad json"),
])
def test_ai_error_triggers_mock_fallback(exc: Exception) -> None:
    fp = FallbackProvider(_primary(raises=exc))
    result = fp.analyse(ITEM, timeout=5.0)

    assert isinstance(result, str)  # mock returns JSON string
    import json
    parsed = json.loads(result)
    assert "category" in parsed
    assert "priority" in parsed


# --- Fallback on unexpected exceptions ---------------------------------------


def test_unexpected_exception_triggers_mock_fallback() -> None:
    fp = FallbackProvider(_primary(raises=RuntimeError("exploded")))
    result = fp.analyse(ITEM, timeout=5.0)

    assert isinstance(result, str)
    import json
    parsed = json.loads(result)
    assert "category" in parsed


# --- Factory integration -----------------------------------------------------


@override_settings(
    AI_PROVIDER="anthropic",
    AI_API_KEY="test-key",
    AI_MODEL="claude-haiku-4-5-20251001",
    AI_FALLBACK_TO_MOCK=True,
)
def test_factory_wraps_anthropic_when_fallback_enabled() -> None:
    provider = get_ai_provider()
    assert isinstance(provider, FallbackProvider)
    assert provider.name == "anthropic+mock"


@override_settings(
    AI_PROVIDER="langchain",
    LANGCHAIN_BACKEND="anthropic",
    AI_API_KEY="test-key",
    AI_MODEL="claude-haiku-4-5-20251001",
    AI_FALLBACK_TO_MOCK=True,
)
def test_factory_wraps_langchain_when_fallback_enabled() -> None:
    provider = get_ai_provider()
    assert isinstance(provider, FallbackProvider)
    assert provider.name == "langchain+mock"


@override_settings(AI_PROVIDER="mock", AI_FALLBACK_TO_MOCK=True)
def test_factory_does_not_double_wrap_mock() -> None:
    provider = get_ai_provider()
    assert isinstance(provider, MockProvider)
    assert not isinstance(provider, FallbackProvider)


@override_settings(
    AI_PROVIDER="anthropic",
    AI_API_KEY="test-key",
    AI_MODEL="claude-haiku-4-5-20251001",
    AI_FALLBACK_TO_MOCK=False,
)
def test_factory_no_wrap_when_fallback_disabled() -> None:
    from work_items.ai.anthropic_provider import AnthropicProvider
    provider = get_ai_provider()
    assert isinstance(provider, AnthropicProvider)
    assert not isinstance(provider, FallbackProvider)
