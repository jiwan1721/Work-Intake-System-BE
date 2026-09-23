"""BE-05 / PLAN §10 #12: tolerant parsing, strict validation."""

from __future__ import annotations

import pytest

from work_items.ai.base import AIInvalidOutputError
from work_items.ai.parser import parse_analysis
from work_items.ai.schema import Category, Priority

GOOD_PAYLOAD = {
    "category": "DOCUMENT_REQUEST",
    "priority": "HIGH",
    "summary": "The applicant needs to provide their latest payslip.",
    "recommendedAction": "Request the missing payslip from the applicant.",
}


@pytest.mark.parametrize(
    ("label", "raw"),
    [
        (
            "plain json",
            '{"category":"DOCUMENT_REQUEST","priority":"HIGH",'
            '"summary":"Needs a payslip.","recommendedAction":"Ask for it."}',
        ),
        ("a dict", GOOD_PAYLOAD),
        (
            "fenced json",
            '```json\n{"category":"COMPLAINT","priority":"URGENT",'
            '"summary":"Unhappy customer.","recommendedAction":"Call them."}\n```',
        ),
        (
            "fenced without a language tag",
            '```\n{"category":"COMPLAINT","priority":"LOW",'
            '"summary":"Minor gripe.","recommendedAction":"Log it."}\n```',
        ),
        (
            "prose on both sides",
            "Sure! Here is the analysis you asked for:\n"
            '{"category":"TECHNICAL_ISSUE","priority":"MEDIUM",'
            '"summary":"Login is broken.","recommendedAction":"Escalate to engineering."}\n'
            "Let me know if you need anything else.",
        ),
        (
            "lowercase enums",
            '{"category":"account_change","priority":"medium",'
            '"summary":"Address change.","recommendedAction":"Update the record."}',
        ),
        (
            "padded enums and text",
            '{"category":"  Document_Request  ","priority":" high ",'
            '"summary":"  Needs a payslip.  ","recommendedAction":"  Ask for it.  "}',
        ),
        (
            "a nested object in a field does not confuse brace matching",
            'Analysis: {"category":"OTHER","priority":"LOW",'
            '"summary":"Mentions a {brace} in text.","recommendedAction":"Nothing to do."}',
        ),
        (
            "extra keys are ignored",
            '{"category":"OTHER","priority":"LOW","summary":"Fine.",'
            '"recommendedAction":"Nothing.","confidence":0.92,"tokens":123}',
        ),
        (
            "snake_case key is accepted as well as the alias",
            '{"category":"OTHER","priority":"LOW","summary":"Fine.",'
            '"recommended_action":"Nothing."}',
        ),
    ],
)
def test_valid_output_is_parsed(label: str, raw: str | dict) -> None:
    result = parse_analysis(raw)

    assert isinstance(result.category, Category)
    assert isinstance(result.priority, Priority)
    assert result.summary
    assert result.recommended_action


def test_values_are_normalised() -> None:
    result = parse_analysis(
        '{"category":" document_request ","priority":"high",'
        '"summary":"  Needs a payslip.  ","recommendedAction":"Ask for it."}'
    )

    assert result.category is Category.DOCUMENT_REQUEST
    assert result.priority is Priority.HIGH
    assert result.summary == "Needs a payslip."


@pytest.mark.parametrize(
    ("label", "raw"),
    [
        ("not json at all", "I'm sorry, I can't help with that."),
        ("empty string", ""),
        ("whitespace only", "   \n\t "),
        (
            "a json array",
            '[{"category":"OTHER","priority":"LOW","summary":"a","recommendedAction":"b"}]',
        ),
        ("a bare string", '"just a string"'),
        ("a number", "42"),
        ("null", "null"),
        (
            "missing a required field",
            '{"category":"OTHER","priority":"LOW","summary":"Missing the action."}',
        ),
        (
            "empty summary",
            '{"category":"OTHER","priority":"LOW","summary":"","recommendedAction":"Something."}',
        ),
        (
            "whitespace-only summary collapses to empty",
            '{"category":"OTHER","priority":"LOW","summary":"   ",'
            '"recommendedAction":"Something."}',
        ),
        (
            "unknown category",
            '{"category":"SPAM","priority":"LOW","summary":"a","recommendedAction":"b"}',
        ),
        (
            "unknown priority",
            '{"category":"OTHER","priority":"CATASTROPHIC","summary":"a","recommendedAction":"b"}',
        ),
        (
            "summary over 500 characters",
            '{"category":"OTHER","priority":"LOW","summary":"' + "x" * 501 + '",'
            '"recommendedAction":"b"}',
        ),
        (
            "truncated json",
            '{"category":"OTHER","priority":"LOW","summary":"a"',
        ),
    ],
)
def test_invalid_output_raises_invalid_output_error(label: str, raw: str) -> None:
    with pytest.raises(AIInvalidOutputError) as exc_info:
        parse_analysis(raw)

    error = exc_info.value
    assert error.code == "INVALID_OUTPUT"
    assert error.message, "the failure needs a readable reason"
    # The raw answer is preserved for the attempt log.
    assert error.raw_output is not None


def test_unknown_category_is_rejected_not_coerced_to_other() -> None:
    """PLAN §7: silent coercion would hide a misbehaving model."""
    with pytest.raises(AIInvalidOutputError) as exc_info:
        parse_analysis('{"category":"SPAM","priority":"LOW","summary":"a","recommendedAction":"b"}')

    assert "category" in exc_info.value.message


def test_raw_output_is_kept_on_the_error_for_debugging() -> None:
    raw = "the model rambled instead of answering"

    with pytest.raises(AIInvalidOutputError) as exc_info:
        parse_analysis(raw)

    assert exc_info.value.raw_output == raw
