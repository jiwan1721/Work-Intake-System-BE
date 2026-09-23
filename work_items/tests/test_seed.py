"""BE-13: the seed command."""

from __future__ import annotations

import re
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import override_settings

from work_items.domain.status import WorkItemStatus
from work_items.management.commands.seed_work_items import SEED_ITEMS
from work_items.models import WorkItem

pytestmark = pytest.mark.django_db

MARKER_RE = re.compile(r"\[simulate:([a-z_]+)\]")


def seed(**kwargs) -> str:
    out = StringIO()
    call_command("seed_work_items", stdout=out, **kwargs)
    return out.getvalue()


def test_seeding_creates_about_ten_items() -> None:
    output = seed()

    assert WorkItem.objects.count() == len(SEED_ITEMS)
    assert WorkItem.objects.count() >= 10
    assert f"{len(SEED_ITEMS)} created" in output


def test_every_simulated_failure_mode_has_an_item() -> None:
    seed()

    markers = {
        match.group(1)
        for title in WorkItem.objects.values_list("title", flat=True)
        if (match := MARKER_RE.search(title))
    }

    assert markers == {"timeout", "malformed", "bad_enum", "error"}


def test_seeding_twice_creates_no_duplicates() -> None:
    seed()
    output = seed()

    assert WorkItem.objects.count() == len(SEED_ITEMS)
    assert f"{len(SEED_ITEMS)} already present" in output


def test_seeded_items_start_as_received() -> None:
    seed()

    assert set(WorkItem.objects.values_list("status", flat=True)) == {WorkItemStatus.RECEIVED}


def test_seeded_items_go_through_intake_so_they_have_content_hashes() -> None:
    seed()

    assert all(WorkItem.objects.exclude(content_hash="").values_list("id", flat=True))
    assert WorkItem.objects.filter(content_hash="").count() == 0


@override_settings(AI_PROVIDER="mock", MOCK_AI_LATENCY_MS=0, MOCK_AI_FAILURE_MODE="none")
def test_analyse_flag_exercises_every_path() -> None:
    seed(analyse=True)

    statuses = set(WorkItem.objects.values_list("status", flat=True))

    # The realistic items succeed; the four [simulate:*] ones fail. Both
    # states must be reachable from a fresh seed so a reviewer can click them.
    assert statuses == {WorkItemStatus.READY_FOR_REVIEW, WorkItemStatus.FAILED}

    failure_codes = set(
        WorkItem.objects.filter(status=WorkItemStatus.FAILED).values_list(
            "last_error_code", flat=True
        )
    )
    assert failure_codes == {"TIMEOUT", "INVALID_OUTPUT", "PROVIDER_ERROR"}
