"""Fallback wrapper: delegates to a primary provider, falls back to mock on any error.

Enabled by setting AI_FALLBACK_TO_MOCK=True. The primary provider's name is
preserved in the wrapper's name (e.g. "langchain+mock") so attempt rows make
clear that the fallback path is live on this installation.
"""

from __future__ import annotations

import logging

from .base import AIProvider, WorkItemInput
from .mock_provider import MockProvider

logger = logging.getLogger(__name__)


class FallbackProvider:
    """Wraps any AIProvider; on failure silently substitutes MockProvider.

    Both AIError subclasses and unexpected exceptions trigger the fallback so
    that a misconfigured or temporarily unreachable primary never leaves an
    item stuck in ANALYSING.
    """

    def __init__(self, primary: AIProvider) -> None:
        self._primary = primary
        self._mock = MockProvider()
        self.name = f"{primary.name}+mock"
        self.model = primary.model

    def analyse(self, item: WorkItemInput, *, timeout: float) -> str | dict:
        try:
            return self._primary.analyse(item, timeout=timeout)
        except Exception as exc:
            logger.warning(
                "Primary provider %r failed (%s: %s); falling back to mock.",
                self._primary.name,
                type(exc).__name__,
                exc,
            )
            return self._mock.analyse(item, timeout=timeout)
