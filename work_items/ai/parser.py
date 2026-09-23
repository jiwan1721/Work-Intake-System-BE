"""Turn whatever the model said into an `AnalysisResult`, or fail cleanly.

Tolerant about *presentation*, strict about *content*:

- accepted: a bare JSON object, a ```json fenced block, a sentence of prose
  either side of the object, lowercase enum values, stray whitespace
- rejected: anything that is not a single JSON object, and any object that
  does not satisfy `AnalysisResult`

Every failure leaves here as `AIInvalidOutputError` carrying the raw text. A
`ValidationError` or `JSONDecodeError` must never escape this module, because
callers are written against the four `AIError` codes and nothing else.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from .base import AIInvalidOutputError
from .schema import AnalysisResult

#: Enough of the model's answer to debug with, without filling the database.
_RAW_EXCERPT_CHARS = 500


def parse_analysis(raw: str | dict) -> AnalysisResult:
    """Validate model output. Raises `AIInvalidOutputError` on anything unusable."""
    raw_text = raw if isinstance(raw, str) else json.dumps(raw, default=str)

    payload = raw if isinstance(raw, dict) else _extract_json_object(raw, raw_text)

    if not isinstance(payload, dict):
        raise AIInvalidOutputError(
            f"Expected a JSON object, got {type(payload).__name__}.", raw_output=raw_text
        )

    try:
        return AnalysisResult.model_validate(payload)
    except ValidationError as exc:
        raise AIInvalidOutputError(
            f"Model output did not match the analysis schema: {_summarise(exc)}",
            raw_output=raw_text,
        ) from exc


def _extract_json_object(raw: str, raw_text: str) -> Any:
    text = raw.strip()
    if not text:
        raise AIInvalidOutputError("Model returned an empty response.", raw_output=raw_text)

    for candidate in _candidates(text):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    raise AIInvalidOutputError("Model output was not valid JSON.", raw_output=raw_text)


def _candidates(text: str) -> list[str]:
    """Progressively more forgiving readings of the same answer."""
    candidates = [text]

    fenced = _strip_code_fence(text)
    if fenced != text:
        candidates.append(fenced)

    for source in (text, fenced):
        block = _first_json_object(source)
        if block is not None:
            candidates.append(block)

    return candidates


def _strip_code_fence(text: str) -> str:
    """Remove a leading ```json / ``` fence and its closing counterpart."""
    if not text.startswith("```"):
        return text
    without_open = text[3:]
    # An opening fence may carry a language tag on the same line.
    newline = without_open.find("\n")
    if newline != -1:
        first_line = without_open[:newline].strip()
        if first_line.isalpha() or first_line == "":
            without_open = without_open[newline + 1 :]
    closing = without_open.rfind("```")
    if closing != -1:
        without_open = without_open[:closing]
    return without_open.strip()


def _first_json_object(text: str) -> str | None:
    """The first balanced {...} span, ignoring braces inside JSON strings."""
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    return None


def _summarise(exc: ValidationError) -> str:
    """A short, readable reason instead of a wall of Pydantic output."""
    parts = []
    for error in exc.errors()[:3]:
        location = ".".join(str(piece) for piece in error["loc"]) or "<root>"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)[:_RAW_EXCERPT_CHARS]
