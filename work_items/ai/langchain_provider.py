"""LangChain provider — unified LLM access via LCEL (PLAN §7).

A single provider implementation that routes to any LLM backend LangChain
supports: Anthropic (Claude), OpenAI, or NVIDIA NIM. The backend is selected
by `LANGCHAIN_BACKEND` and the API key / model come from the shared
`AI_API_KEY` / `AI_MODEL` settings — nothing new to configure if you have
already used one of the direct providers.

Why LangChain here:
- One code path for every backend; adding a new model is a settings change.
- `.with_structured_output()` forces JSON-schema-validated tool use on every
  backend that supports it, eliminating the "wrote a paragraph instead of JSON"
  failure mode without per-provider prompt hacks.
- The LCEL chain is a seam for future extensions (memory, RAG, callbacks,
  LangSmith tracing) without touching the workflow or service layers.

The output of `.with_structured_output()` is an `AnalysisResult` Pydantic
instance. We convert it to a dict before returning so `parse_analysis` can
run its validation pass exactly as it does for every other provider.

This module is imported only when `AI_PROVIDER=langchain`, so the default
install never needs LangChain and no API key is required to run or test.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured

from .base import AIInvalidOutputError, AIProviderError, AITimeoutError, WorkItemInput
from .prompt import SYSTEM_PROMPT, build_user_message
from .schema import AnalysisResult

BACKEND_ANTHROPIC = "anthropic"
BACKEND_OPENAI = "openai"
BACKEND_NVIDIA = "nvidia"

SUPPORTED_BACKENDS = (BACKEND_ANTHROPIC, BACKEND_OPENAI, BACKEND_NVIDIA)

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

DEFAULT_MODELS = {
    BACKEND_ANTHROPIC: "claude-sonnet-4-6",
    BACKEND_OPENAI: "gpt-4o-mini",
    BACKEND_NVIDIA: "meta/llama-3.1-70b-instruct",
}

MAX_TOKENS = 1024


def _build_llm(backend: str, api_key: str, model: str) -> Any:
    """Construct the LangChain chat model for the requested backend."""
    if backend == BACKEND_ANTHROPIC:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model,
            api_key=api_key,
            max_tokens=MAX_TOKENS,
            temperature=0,
        )

    if backend in (BACKEND_OPENAI, BACKEND_NVIDIA):
        from langchain_openai import ChatOpenAI

        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": api_key,
            "max_tokens": MAX_TOKENS,
            "temperature": 0,
        }
        if backend == BACKEND_NVIDIA:
            kwargs["base_url"] = NVIDIA_BASE_URL

        return ChatOpenAI(**kwargs)

    raise ImproperlyConfigured(
        f"Unknown LANGCHAIN_BACKEND {backend!r}. "
        f"Allowed values: {', '.join(SUPPORTED_BACKENDS)}."
    )


class LangChainProvider:
    """Implements the `AIProvider` protocol via a LangChain LCEL chain.

    The chain:  ChatPromptTemplate  |  LLM.with_structured_output(AnalysisResult)

    `with_structured_output` generates the JSON schema from `AnalysisResult`
    and uses the backend's tool-calling API to guarantee a structured response.
    The validated Pydantic model is then converted to a dict so `parse_analysis`
    can run its own validation pass, keeping the failure path identical for all
    providers.
    """

    name = "langchain"

    def __init__(
        self,
        *,
        backend: str,
        api_key: str | None = None,
        model: str | None = None,
        # Accepts a pre-built LLM for testing so no API key is ever needed.
        llm: Any | None = None,
    ) -> None:
        self.model = model or DEFAULT_MODELS.get(backend, "")

        if not api_key and llm is None:
            raise ImproperlyConfigured(
                "AI_API_KEY is required when AI_PROVIDER=langchain. "
                f"Set it in .env (never commit it), or use AI_PROVIDER=mock."
            )

        from langchain_core.messages import SystemMessage
        from langchain_core.prompts import ChatPromptTemplate

        chat_llm = llm or _build_llm(backend, api_key, self.model)  # type: ignore[arg-type]
        structured_llm = chat_llm.with_structured_output(AnalysisResult)

        # SYSTEM_PROMPT contains literal JSON braces that LangChain would
        # misparse as template variables if passed as a ("system", ...) tuple.
        # Wrapping it in SystemMessage skips template interpolation entirely.
        self._prompt = ChatPromptTemplate.from_messages(
            [
                SystemMessage(content=SYSTEM_PROMPT),
                ("human", "{user_message}"),
            ]
        )
        self._chain = self._prompt | structured_llm

    def analyse(self, item: WorkItemInput, *, timeout: float) -> str | dict:
        user_message = build_user_message(item)

        try:
            result: AnalysisResult = self._chain.invoke(
                {"user_message": user_message},
                config={"timeout": timeout},
            )
        except Exception as exc:
            _map_exception(exc, timeout)
            raise  # unreachable, but satisfies type checkers

        if not isinstance(result, AnalysisResult):
            # with_structured_output returned something unexpected; let the
            # parser reject it cleanly rather than crashing here.
            return str(result)

        # Convert to camelCase dict so parse_analysis handles it identically
        # to Anthropic's tool-call dict and NVIDIA's json_object response.
        return result.model_dump(by_alias=True)


def _map_exception(exc: Exception, timeout: float) -> None:
    """Re-raise exc as the appropriate AIError subclass.

    LangChain wraps the underlying SDK exceptions but lets them propagate, so
    we can match against the SDK types directly.
    """
    exc_str = str(type(exc).__name__).lower()
    exc_msg = str(exc)

    # Timeout — both Anthropic and OpenAI SDK flavours.
    try:
        import anthropic as _anthropic

        if isinstance(exc, _anthropic.APITimeoutError):
            raise AITimeoutError(
                f"LangChain/Anthropic did not respond within {timeout:.0f}s."
            ) from exc
        if isinstance(exc, _anthropic.APIStatusError):
            raise AIProviderError(
                f"LangChain/Anthropic returned HTTP {exc.status_code}."
            ) from exc
        if isinstance(exc, _anthropic.APIError):
            raise AIProviderError(f"LangChain/Anthropic request failed: {exc}") from exc
    except ImportError:
        pass

    try:
        import openai as _openai

        if isinstance(exc, _openai.APITimeoutError):
            raise AITimeoutError(
                f"LangChain/OpenAI did not respond within {timeout:.0f}s."
            ) from exc
        if isinstance(exc, _openai.APIStatusError):
            raise AIProviderError(
                f"LangChain/OpenAI returned HTTP {exc.status_code}."
            ) from exc
        if isinstance(exc, _openai.APIError):
            raise AIProviderError(f"LangChain/OpenAI request failed: {exc}") from exc
    except ImportError:
        pass

    # LangChain output-parser failure (schema mismatch after tool-call).
    try:
        from langchain_core.exceptions import OutputParserException

        if isinstance(exc, OutputParserException):
            raise AIInvalidOutputError(
                f"LangChain output did not match the analysis schema: {exc}",
                raw_output=exc_msg,
            ) from exc
    except ImportError:
        pass

    # Generic timeout detection via message content (covers httpx/requests).
    lower = exc_msg.lower()
    if "timeout" in lower or "timed out" in lower:
        raise AITimeoutError(
            f"LangChain request timed out after {timeout:.0f}s."
        ) from exc

    # Anything else is an unexpected provider error.
    raise AIProviderError(f"LangChain request failed: {exc}") from exc
