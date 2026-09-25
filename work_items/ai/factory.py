"""Provider selection (PLAN §7).

The factory is the only place that reads settings, which keeps the adapters
themselves plain objects that tests can construct with explicit arguments.
Services take a provider as a parameter and fall back to this, so a test
injects a fake without patching anything global.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import AIProvider
from .mock_provider import FAILURE_NONE, MockProvider

MOCK = "mock"
ANTHROPIC = "anthropic"
NVIDIA = "nvidia"
LANGCHAIN = "langchain"

ALLOWED_PROVIDERS = (MOCK, ANTHROPIC, NVIDIA, LANGCHAIN)


def get_ai_provider(name: str | None = None) -> AIProvider:
    """Build the provider named by `AI_PROVIDER` (default: mock).

    When AI_FALLBACK_TO_MOCK=True the returned provider is wrapped in
    FallbackProvider so any failure silently substitutes the mock.
    """
    provider_name = (name or settings.AI_PROVIDER or MOCK).strip().lower()

    if provider_name == MOCK:
        return MockProvider(
            latency_ms=getattr(settings, "MOCK_AI_LATENCY_MS", 0),
            failure_mode=getattr(settings, "MOCK_AI_FAILURE_MODE", FAILURE_NONE),
        )

    if provider_name == ANTHROPIC:
        # Imported lazily: the SDK is an optional dependency and the default
        # install never needs it.
        from .anthropic_provider import AnthropicProvider

        return _wrap_with_fallback(AnthropicProvider(
            api_key=settings.AI_API_KEY,
            model=settings.AI_MODEL or None,
        ))

    if provider_name == NVIDIA:
        # Imported lazily: the SDK is an optional dependency and the default
        # install never needs it.
        from .nvidia_provider import NvidiaProvider

        return _wrap_with_fallback(NvidiaProvider(
            api_key=settings.AI_API_KEY,
            model=settings.AI_MODEL or None,
        ))

    if provider_name == LANGCHAIN:
        # Imported lazily: LangChain and its backend SDKs are optional.
        from .langchain_provider import LangChainProvider

        return _wrap_with_fallback(LangChainProvider(
            backend=getattr(settings, "LANGCHAIN_BACKEND", "anthropic"),
            api_key=settings.AI_API_KEY,
            model=settings.AI_MODEL or None,
        ))

    raise ImproperlyConfigured(
        f"Unknown AI_PROVIDER {provider_name!r}. Allowed values: {', '.join(ALLOWED_PROVIDERS)}."
    )


def _wrap_with_fallback(provider: AIProvider) -> AIProvider:
    """Return provider wrapped in FallbackProvider if AI_FALLBACK_TO_MOCK is set."""
    if getattr(settings, "AI_FALLBACK_TO_MOCK", False) and provider.name != MOCK:
        from .fallback_provider import FallbackProvider

        return FallbackProvider(provider)
    return provider
