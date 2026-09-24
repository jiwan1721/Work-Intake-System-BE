"""NVIDIA NIM provider — OpenAI-compatible endpoint (PLAN §7).

NVIDIA NIM exposes an OpenAI-compatible chat completions API at
https://integrate.api.nvidia.com/v1. We use `response_format={"type":
"json_object"}` to get a JSON response directly, which avoids most of the
"wrote a paragraph instead of JSON" failure mode that plain text produces.

The same `parse_analysis` pipeline that validates Anthropic's tool-call dict
also validates this output, so all failure paths are identical regardless of
provider. Unknown enum values from the model are rejected, never coerced.

This module is imported only when `AI_PROVIDER=nvidia`, so the default install
never needs the openai SDK and no API key is required to run or test anything.
"""

from __future__ import annotations

import json
from typing import Any

import openai
from django.core.exceptions import ImproperlyConfigured

from .base import AIProviderError, AITimeoutError, WorkItemInput
from .prompt import SYSTEM_PROMPT, build_user_message

DEFAULT_MODEL = "meta/llama-3.1-70b-instruct"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
MAX_TOKENS = 1024


class NvidiaProvider:
    """Implements the `AIProvider` protocol against the NVIDIA NIM API."""

    name = "nvidia"

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
                "AI_API_KEY is required when AI_PROVIDER=nvidia. "
                "Get a key at https://build.nvidia.com, set it in .env (never commit it), "
                "or use AI_PROVIDER=mock."
            )
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=NVIDIA_BASE_URL,
            max_retries=1,
        )

    def analyse(self, item: WorkItemInput, *, timeout: float) -> str | dict:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                # Triage should be reproducible, not creative.
                temperature=0,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_message(item)},
                ],
                response_format={"type": "json_object"},
                timeout=timeout,
            )
        except openai.APITimeoutError as exc:
            raise AITimeoutError(f"NVIDIA NIM did not respond within {timeout:.0f}s.") from exc
        except openai.RateLimitError as exc:
            raise AIProviderError("NVIDIA NIM rate limit exceeded.") from exc
        except openai.APIStatusError as exc:
            raise AIProviderError(f"NVIDIA NIM returned HTTP {exc.status_code}.") from exc
        except openai.APIConnectionError as exc:
            raise AIProviderError(f"NVIDIA NIM connection failed: {exc}") from exc
        except openai.APIError as exc:
            raise AIProviderError(f"NVIDIA NIM request failed: {exc}") from exc

        return _extract_content(response)


def _extract_content(response: Any) -> str | dict:
    """Pull usable content from the chat completion response.

    When `response_format=json_object` succeeds, the content is already a JSON
    string. Parse it eagerly so the caller gets a dict (same path as Anthropic's
    tool-call result) and `parse_analysis` never has to guess the format.
    """
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise AIProviderError("NVIDIA NIM returned a response with no choices.")

    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None) if message else None

    if not content:
        raise AIProviderError("NVIDIA NIM returned a response with no content.")

    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # Fall back to the raw string; parse_analysis handles all text formats.
    return content
