"""Tests for the LangChain provider and its factory integration.

No network and no API key: all tests drive a stub LLM that returns a
pre-built AnalysisResult, exactly as the Anthropic adapter tests do.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

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
from work_items.ai.factory import get_ai_provider
from work_items.ai.langchain_provider import (
    BACKEND_ANTHROPIC,
    BACKEND_NVIDIA,
    BACKEND_OPENAI,
    LangChainProvider,
    _build_llm,
)
from work_items.ai.parser import parse_analysis
from work_items.ai.schema import AnalysisResult, Category, Priority

PAYSLIP = WorkItemInput(
    external_id="CRM-LC-1",
    title="Missing income document",
    description="The applicant submitted their application but no payslip was attached.",
)

GOOD_RESULT = AnalysisResult(
    category=Category.DOCUMENT_REQUEST,
    priority=Priority.HIGH,
    summary="Applicant has not submitted a payslip.",
    recommendedAction="Request the payslip from the applicant.",
)


def _make_stub_llm(returns: Any = GOOD_RESULT, raises: Exception | None = None) -> Any:
    """Return a LangChain-compatible LLM stub that bypasses the network.

    `with_structured_output()` must return a real LangChain Runnable so that
    the LCEL `|` operator can compose it with the ChatPromptTemplate correctly.
    RunnableLambda is the lightest Runnable that lets us control the output.
    """
    from langchain_core.runnables import RunnableLambda

    def _run(_input: Any) -> Any:  # input is the formatted ChatPromptValue; ignored
        if raises is not None:
            raise raises
        return returns

    stub = MagicMock()
    stub.with_structured_output.return_value = RunnableLambda(_run)
    return stub


def _provider(returns: Any = GOOD_RESULT, raises: Exception | None = None) -> LangChainProvider:
    return LangChainProvider(
        backend=BACKEND_ANTHROPIC,
        llm=_make_stub_llm(returns=returns, raises=raises),
    )


# --- Protocol compliance ---------------------------------------------------


def test_langchain_provider_satisfies_the_protocol() -> None:
    p = _provider()
    assert isinstance(p, AIProvider)
    assert p.name == "langchain"


# --- Happy path ------------------------------------------------------------


def test_analyse_returns_a_dict_matching_analysis_result() -> None:
    result = _provider().analyse(PAYSLIP, timeout=5.0)

    assert isinstance(result, dict)
    parsed = parse_analysis(result)
    assert parsed.category is Category.DOCUMENT_REQUEST
    assert parsed.priority is Priority.HIGH


def test_analyse_result_survives_parse_analysis() -> None:
    raw = _provider().analyse(PAYSLIP, timeout=5.0)
    result = parse_analysis(raw)

    assert result.summary
    assert result.recommended_action


def test_dict_uses_camel_case_aliases() -> None:
    raw = _provider().analyse(PAYSLIP, timeout=5.0)

    assert isinstance(raw, dict)
    assert "recommendedAction" in raw
    assert "recommended_action" not in raw


# --- Non-AnalysisResult fallback ------------------------------------------


def test_non_pydantic_return_is_stringified_for_parse_analysis() -> None:
    # If with_structured_output returns something unexpected, analyse()
    # stringifies it so parse_analysis can reject it cleanly.
    p = _provider(returns={"unexpected": "shape"})
    raw = p.analyse(PAYSLIP, timeout=5.0)

    # A plain dict is still a valid str|dict input to parse_analysis, but it
    # won't match AnalysisResult's schema, so it should be rejected.
    with pytest.raises(Exception):
        parse_analysis(raw)


# --- Error mapping ---------------------------------------------------------


def test_anthropic_timeout_becomes_ai_timeout_error() -> None:
    import anthropic

    p = _provider(raises=anthropic.APITimeoutError(request=MagicMock()))
    with pytest.raises(AITimeoutError):
        p.analyse(PAYSLIP, timeout=5.0)


def test_anthropic_status_error_becomes_ai_provider_error() -> None:
    import anthropic

    err = anthropic.APIStatusError(
        message="Rate limited", response=MagicMock(status_code=429), body={}
    )
    p = _provider(raises=err)
    with pytest.raises(AIProviderError):
        p.analyse(PAYSLIP, timeout=5.0)


def test_openai_timeout_becomes_ai_timeout_error() -> None:
    import openai

    p = _provider(raises=openai.APITimeoutError(request=MagicMock()))
    with pytest.raises(AITimeoutError):
        p.analyse(PAYSLIP, timeout=5.0)


def test_langchain_output_parser_exception_becomes_invalid_output_error() -> None:
    from langchain_core.exceptions import OutputParserException

    p = _provider(raises=OutputParserException("schema mismatch"))
    with pytest.raises(AIInvalidOutputError):
        p.analyse(PAYSLIP, timeout=5.0)


def test_generic_timeout_message_becomes_ai_timeout_error() -> None:
    p = _provider(raises=RuntimeError("Connection timed out after 20 seconds"))
    with pytest.raises(AITimeoutError):
        p.analyse(PAYSLIP, timeout=20.0)


def test_unknown_exception_becomes_ai_provider_error() -> None:
    p = _provider(raises=RuntimeError("Something exploded"))
    with pytest.raises(AIProviderError):
        p.analyse(PAYSLIP, timeout=5.0)


# --- Missing API key -------------------------------------------------------


def test_langchain_provider_without_key_and_without_stub_fails() -> None:
    with pytest.raises(ImproperlyConfigured, match="AI_API_KEY"):
        LangChainProvider(backend=BACKEND_ANTHROPIC, api_key="")


# --- _build_llm backend selection ------------------------------------------


def test_build_llm_anthropic_returns_chat_anthropic() -> None:
    from langchain_anthropic import ChatAnthropic

    llm = _build_llm(BACKEND_ANTHROPIC, api_key="test-key", model="claude-haiku-4-5-20251001")
    assert isinstance(llm, ChatAnthropic)


def test_build_llm_openai_returns_chat_openai() -> None:
    from langchain_openai import ChatOpenAI

    llm = _build_llm(BACKEND_OPENAI, api_key="test-key", model="gpt-4o-mini")
    assert isinstance(llm, ChatOpenAI)
    assert llm.openai_api_base is None  # no custom base_url for plain OpenAI


def test_build_llm_nvidia_sets_nvidia_base_url() -> None:
    from langchain_openai import ChatOpenAI

    from work_items.ai.langchain_provider import NVIDIA_BASE_URL

    llm = _build_llm(BACKEND_NVIDIA, api_key="nvapi-test", model="meta/llama-3.1-70b-instruct")
    assert isinstance(llm, ChatOpenAI)
    assert str(llm.openai_api_base) == NVIDIA_BASE_URL


def test_build_llm_unknown_backend_raises() -> None:
    with pytest.raises(ImproperlyConfigured):
        _build_llm("telepathy", api_key="x", model="x")


# --- Factory integration ---------------------------------------------------


@override_settings(
    AI_PROVIDER="langchain",
    LANGCHAIN_BACKEND="anthropic",
    AI_API_KEY="test-key",
    AI_MODEL="claude-haiku-4-5-20251001",
)
def test_factory_builds_langchain_provider() -> None:
    provider = get_ai_provider()
    assert isinstance(provider, LangChainProvider)
    assert provider.name == "langchain"


@override_settings(AI_PROVIDER="langchain", LANGCHAIN_BACKEND="anthropic", AI_API_KEY="", AI_MODEL="")
def test_factory_langchain_without_key_raises() -> None:
    with pytest.raises(ImproperlyConfigured, match="AI_API_KEY"):
        get_ai_provider()
