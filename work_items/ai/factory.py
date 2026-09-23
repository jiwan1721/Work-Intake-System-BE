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

ALLOWED_PROVIDERS = (MOCK, ANTHROPIC)


def get_ai_provider(name: str | None = None) -> AIProvider:
    """Build the provider named by `AI_PROVIDER` (default: mock)."""
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

        return AnthropicProvider(
            api_key=settings.AI_API_KEY,
            model=settings.AI_MODEL or None,
        )

    raise ImproperlyConfigured(
        f"Unknown AI_PROVIDER {provider_name!r}. Allowed values: {', '.join(ALLOWED_PROVIDERS)}."
    )
