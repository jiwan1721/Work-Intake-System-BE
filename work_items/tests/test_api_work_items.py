"""BE-11, BE-12, BE-14 / PLAN §10 #1, #3, #5, #10, #11, #13: the endpoints."""

from __future__ import annotations

import threading
from typing import Any

import pytest
from django.db import connection
from django.test import override_settings
from rest_framework.test import APIClient

from work_items.ai.base import STALE_ANALYSIS_CODE, AIProviderError, AITimeoutError, WorkItemInput
from work_items.domain.status import WorkItemStatus
from work_items.models import AnalysisAttempt, WorkItem
from work_items.services.analysis import reap_stale_analyses

from .factories import make_work_item
from .test_api_skeleton import assert_envelope

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/work-items"

# Exactly the keys in the PLAN §8 example response.
PLAN_ITEM_KEYS = {
    "id",
    "externalId",
    "external_id",
    "title",
    "description",
    "status",
    "analysis",
    "lastError",
    "attemptCount",
    "allowedActions",
    "version",
    "createdAt",
    "updatedAt",
}

PAYLOAD = {
    "externalId": "CRM-12345",
    "title": "Missing income document",
    "description": "The applicant submitted their application but no payslip was attached.",
}

GOOD_JSON = (
    '{"category":"DOCUMENT_REQUEST","priority":"HIGH",'
    '"summary":"Needs a payslip.","recommendedAction":"Request the payslip."}'
)


@pytest.fixture
def client(django_user_model) -> APIClient:
    # force_authenticate bypasses JWT so tests don't need real tokens.
    user = django_user_model.objects.create_user(
        email="operator@test.local", password="testpass123"
    )
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


class FakeProvider:
    name = "fake"
    model = "fake-v1"

    def __init__(self, *, returns: Any = GOOD_JSON, raises: Exception | None = None) -> None:
        self.returns = returns
        self.raises = raises
        self.call_count = 0
        self._lock = threading.Lock()

    def analyse(self, item: WorkItemInput, *, timeout: float) -> str | dict:
        with self._lock:
            self.call_count += 1
        if self.raises is not None:
            raise self.raises
        return self.returns


@pytest.fixture
def provider(monkeypatch) -> FakeProvider:
    """Make every endpoint use one provider the test can inspect."""
    fake = FakeProvider()
    monkeypatch.setattr(
        "work_items.services.analysis.get_ai_provider", lambda *args, **kwargs: fake
    )
    return fake


# --- POST /work-items (intake) ---------------------------------------------


def test_new_item_returns_201_with_a_location_header(client: APIClient) -> None:
    response = client.post(LIST_URL, PAYLOAD, format="json")

    assert response.status_code == 201
    body = response.json()
    assert body["externalId"] == "CRM-12345"
    assert body["status"] == WorkItemStatus.RECEIVED
    assert response["Location"] == f"/api/v1/work-items/{body['id']}"


def test_the_item_response_matches_the_plan_shape(client: APIClient) -> None:
    body = client.post(LIST_URL, PAYLOAD, format="json").json()

    assert set(body) == PLAN_ITEM_KEYS
    assert body["analysis"] is None
    assert body["lastError"] is None
    assert body["attemptCount"] == 0
    assert body["allowedActions"] == ["analyse"]
    assert body["version"] == 1


def test_replaying_the_same_payload_returns_200_and_no_new_row(client: APIClient) -> None:
    first = client.post(LIST_URL, PAYLOAD, format="json")
    second = client.post(LIST_URL, PAYLOAD, format="json")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert "Location" not in second
    assert WorkItem.objects.count() == 1


def test_same_external_id_with_different_content_is_409(client: APIClient) -> None:
    client.post(LIST_URL, PAYLOAD, format="json")

    response = client.post(
        LIST_URL,
        {**PAYLOAD, "description": "Something completely different."},
        format="json",
    )

    assert response.status_code == 409
    error = assert_envelope(response.json(), "DUPLICATE_CONFLICT")
    assert error["details"]["externalId"] == "CRM-12345"

    stored = WorkItem.objects.get(external_id="CRM-12345")
    assert stored.description == PAYLOAD["description"]


@pytest.mark.parametrize(
    ("payload", "bad_field"),
    [
        ({**PAYLOAD, "externalId": ""}, "externalId"),
        ({**PAYLOAD, "title": "   "}, "title"),
        ({**PAYLOAD, "description": ""}, "description"),
        ({**PAYLOAD, "title": "x" * 201}, "title"),
        ({**PAYLOAD, "description": "x" * 10_001}, "description"),
        ({"title": "no external id", "description": "..."}, "externalId"),
    ],
)
def test_invalid_payloads_are_400_and_store_nothing(
    client: APIClient, payload: dict, bad_field: str
) -> None:
    response = client.post(LIST_URL, payload, format="json")

    assert response.status_code == 400
    error = assert_envelope(response.json(), "VALIDATION_ERROR")
    assert bad_field in error["details"]
    assert not WorkItem.objects.exists()


def test_surrounding_whitespace_is_trimmed_on_intake(client: APIClient) -> None:
    response = client.post(
        LIST_URL,
        {**PAYLOAD, "title": "  Missing income document  "},
        format="json",
    )

    assert response.json()["title"] == "Missing income document"


# --- GET /work-items (list) ------------------------------------------------


def test_status_filter_returns_only_matching_items(client: APIClient) -> None:
    make_work_item(external_id="CRM-a", status=WorkItemStatus.FAILED)
    make_work_item(external_id="CRM-b", status=WorkItemStatus.RECEIVED)

    body = client.get(f"{LIST_URL}?status=FAILED").json()

    assert body["count"] == 1
    assert body["results"][0]["externalId"] == "CRM-a"


def test_an_invalid_status_filter_is_400(client: APIClient) -> None:
    response = client.get(f"{LIST_URL}?status=BOGUS")

    assert response.status_code == 400
    error = assert_envelope(response.json(), "VALIDATION_ERROR")
    assert error["details"]["status"] == "BOGUS"
    assert WorkItemStatus.FAILED in error["details"]["allowed"]


def test_list_items_carry_the_plan_shape(client: APIClient) -> None:
    make_work_item(external_id="CRM-c")

    results = client.get(LIST_URL).json()["results"]

    assert set(results[0]) == PLAN_ITEM_KEYS


# --- GET /work-items/{id} (detail) -----------------------------------------


def test_detail_adds_attempts_and_transitions(client: APIClient, provider: FakeProvider) -> None:
    item = make_work_item(external_id="CRM-d")
    client.post(f"{LIST_URL}/{item.id}/analyse")

    body = client.get(f"{LIST_URL}/{item.id}").json()

    assert set(body) == PLAN_ITEM_KEYS | {"attempts", "transitions"}
    assert len(body["attempts"]) == 1
    assert body["attempts"][0]["outcome"] == "SUCCEEDED"
    assert body["attempts"][0]["provider"] == "fake"
    # Newest first.
    assert [t["toStatus"] for t in body["transitions"]] == ["READY_FOR_REVIEW", "ANALYSING"]


def test_detail_never_exposes_raw_model_output(client: APIClient, provider: FakeProvider) -> None:
    """Unvalidated model text is debugging material, not a field for the UI."""
    provider.returns = "the model rambled"
    item = make_work_item(external_id="CRM-e")
    client.post(f"{LIST_URL}/{item.id}/analyse")

    body = client.get(f"{LIST_URL}/{item.id}").json()

    assert AnalysisAttempt.objects.get(work_item=item).raw_output == "the model rambled"
    assert "rawOutput" not in body["attempts"][0]
    assert "raw_output" not in body["attempts"][0]


def test_unknown_id_is_404(client: APIClient) -> None:
    response = client.get(f"{LIST_URL}/0f9c6c1e-0000-4000-8000-000000000000")

    assert response.status_code == 404
    assert_envelope(response.json(), "NOT_FOUND")


# --- POST .../analyse ------------------------------------------------------


def test_analyse_returns_200_and_the_analysed_item(
    client: APIClient, provider: FakeProvider
) -> None:
    item = make_work_item(external_id="CRM-f")

    response = client.post(f"{LIST_URL}/{item.id}/analyse")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == WorkItemStatus.READY_FOR_REVIEW
    assert body["analysis"]["category"] == "DOCUMENT_REQUEST"
    assert body["analysis"]["model"] == "fake-v1"
    assert body["allowedActions"] == ["complete"]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (AITimeoutError("slow"), "TIMEOUT"),
        (AIProviderError("503"), "PROVIDER_ERROR"),
    ],
)
def test_an_ai_failure_is_200_with_a_failed_item_not_a_5xx(
    client: APIClient, provider: FakeProvider, error: Exception, code: str
) -> None:
    """PLAN §8: the request was handled correctly; the AI failing is domain state."""
    provider.raises = error
    item = make_work_item(external_id="CRM-g")

    response = client.post(f"{LIST_URL}/{item.id}/analyse")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == WorkItemStatus.FAILED
    assert body["lastError"]["code"] == code
    assert body["analysis"] is None
    assert body["allowedActions"] == ["retry"]


@pytest.mark.parametrize(
    "status",
    [WorkItemStatus.ANALYSING, WorkItemStatus.READY_FOR_REVIEW, WorkItemStatus.COMPLETED],
    ids=str,
)
def test_analyse_from_the_wrong_status_is_409(
    client: APIClient, provider: FakeProvider, status: WorkItemStatus
) -> None:
    item = make_work_item(
        external_id="CRM-h",
        status=status,
        with_analysis=status in {WorkItemStatus.READY_FOR_REVIEW, WorkItemStatus.COMPLETED},
    )

    response = client.post(f"{LIST_URL}/{item.id}/analyse")

    assert response.status_code == 409
    error = assert_envelope(response.json(), "INVALID_TRANSITION")
    assert error["details"]["currentStatus"] == status
    assert provider.call_count == 0


def test_analyse_is_200_even_when_the_reaper_wins(
    client: APIClient, provider: FakeProvider, monkeypatch
) -> None:
    """A result that arrives after the reaper gave up is not the client's problem.

    The request was processed correctly, so it gets 200 and the item as it
    actually is — FAILED and retryable — rather than a conflict.
    """
    item = make_work_item(external_id="CRM-reaped")

    def reap_then_answer(work_item: WorkItemInput, *, timeout: float) -> str:
        reap_stale_analyses(older_than_seconds=0)
        return GOOD_JSON

    monkeypatch.setattr(provider, "analyse", reap_then_answer)

    response = client.post(f"{LIST_URL}/{item.id}/analyse")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == WorkItemStatus.FAILED
    assert body["lastError"]["code"] == STALE_ANALYSIS_CODE
    assert body["analysis"] is None
    assert body["allowedActions"] == ["retry"]


def test_analyse_on_an_unknown_item_is_404(client: APIClient, provider: FakeProvider) -> None:
    response = client.post(f"{LIST_URL}/0f9c6c1e-0000-4000-8000-000000000000/analyse")

    assert response.status_code == 404
    assert_envelope(response.json(), "NOT_FOUND")


# --- POST .../retry --------------------------------------------------------


def test_retry_from_failed_succeeds(client: APIClient, provider: FakeProvider) -> None:
    provider.raises = AITimeoutError("slow")
    item = make_work_item(external_id="CRM-i")
    client.post(f"{LIST_URL}/{item.id}/analyse")

    provider.raises = None
    response = client.post(f"{LIST_URL}/{item.id}/retry")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == WorkItemStatus.READY_FOR_REVIEW
    assert body["attemptCount"] == 2
    assert body["lastError"] is None


@pytest.mark.parametrize(
    "status",
    [WorkItemStatus.RECEIVED, WorkItemStatus.ANALYSING, WorkItemStatus.COMPLETED],
    ids=str,
)
def test_retry_from_a_non_failed_item_is_409_not_retryable(
    client: APIClient, provider: FakeProvider, status: WorkItemStatus
) -> None:
    item = make_work_item(
        external_id="CRM-j",
        status=status,
        with_analysis=status is WorkItemStatus.COMPLETED,
    )

    response = client.post(f"{LIST_URL}/{item.id}/retry")

    assert response.status_code == 409
    assert_envelope(response.json(), "NOT_RETRYABLE")
    assert provider.call_count == 0


@override_settings(AI_MAX_ATTEMPTS=1)
def test_retry_over_the_cap_is_409_max_attempts_reached(
    client: APIClient, provider: FakeProvider
) -> None:
    provider.raises = AIProviderError("503")
    item = make_work_item(external_id="CRM-k")
    client.post(f"{LIST_URL}/{item.id}/analyse")

    response = client.post(f"{LIST_URL}/{item.id}/retry")

    assert response.status_code == 409
    error = assert_envelope(response.json(), "MAX_ATTEMPTS_REACHED")
    assert error["details"]["maxAttempts"] == 1
    assert provider.call_count == 1


# --- PATCH .../status ------------------------------------------------------


def test_completing_a_reviewed_item(client: APIClient) -> None:
    item = make_work_item(
        external_id="CRM-l", status=WorkItemStatus.READY_FOR_REVIEW, with_analysis=True
    )

    response = client.patch(f"{LIST_URL}/{item.id}/status", {"status": "COMPLETED"}, format="json")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == WorkItemStatus.COMPLETED
    assert body["allowedActions"] == []
    assert WorkItem.objects.get(pk=item.id).completed_at is not None


@pytest.mark.parametrize(
    "target",
    [WorkItemStatus.ANALYSING, WorkItemStatus.READY_FOR_REVIEW],
    ids=str,
)
def test_system_only_targets_are_refused(client: APIClient, target: WorkItemStatus) -> None:
    """An operator cannot PATCH an item into a state that requires running work."""
    item = make_work_item(external_id="CRM-m")

    response = client.patch(f"{LIST_URL}/{item.id}/status", {"status": str(target)}, format="json")

    assert response.status_code == 409
    error = assert_envelope(response.json(), "INVALID_TRANSITION")
    assert error["details"]["currentStatus"] == WorkItemStatus.RECEIVED
    assert WorkItem.objects.get(pk=item.id).status == WorkItemStatus.RECEIVED


def test_completing_a_completed_item_is_409(client: APIClient) -> None:
    item = make_work_item(external_id="CRM-n", status=WorkItemStatus.COMPLETED, with_analysis=True)

    response = client.patch(f"{LIST_URL}/{item.id}/status", {"status": "COMPLETED"}, format="json")

    assert response.status_code == 409
    assert_envelope(response.json(), "INVALID_TRANSITION")


def test_an_unknown_status_value_is_400(client: APIClient) -> None:
    item = make_work_item(external_id="CRM-o")

    response = client.patch(f"{LIST_URL}/{item.id}/status", {"status": "ON_HOLD"}, format="json")

    assert response.status_code == 400
    error = assert_envelope(response.json(), "VALIDATION_ERROR")
    assert "status" in error["details"]


# --- Concurrency (PLAN §10 #11) --------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_analyse_requests_call_the_model_once(monkeypatch, django_user_model) -> None:
    fake = FakeProvider()
    monkeypatch.setattr(
        "work_items.services.analysis.get_ai_provider", lambda *args, **kwargs: fake
    )
    item = make_work_item(external_id="CRM-race")
    user = django_user_model.objects.create_user(email="race@test.local", password="pass")

    barrier = threading.Barrier(2, timeout=10)
    statuses: list[int] = []
    lock = threading.Lock()

    def fire() -> None:
        try:
            barrier.wait()
            c = APIClient()
            c.force_authenticate(user=user)
            response = c.post(f"{LIST_URL}/{item.id}/analyse")
            with lock:
                statuses.append(response.status_code)
        finally:
            connection.close()

    threads = [threading.Thread(target=fire) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(statuses) == [200, 409], f"expected one winner and one conflict, got {statuses}"
    assert fake.call_count == 1
    assert AnalysisAttempt.objects.filter(work_item=item).count() == 1


# --- Intake API key (BE-14) ------------------------------------------------


@override_settings(INTAKE_API_KEY="")
def test_intake_is_open_when_no_key_is_configured(client: APIClient) -> None:
    assert client.post(LIST_URL, PAYLOAD, format="json").status_code == 201


@override_settings(INTAKE_API_KEY="s3cret")
def test_intake_without_a_key_is_401(client: APIClient) -> None:
    response = client.post(LIST_URL, PAYLOAD, format="json")

    assert response.status_code == 401
    assert_envelope(response.json(), "UNAUTHORIZED")
    assert not WorkItem.objects.exists()


@override_settings(INTAKE_API_KEY="s3cret")
def test_intake_with_a_wrong_key_is_401(client: APIClient) -> None:
    response = client.post(LIST_URL, PAYLOAD, format="json", headers={"X-API-Key": "guess"})

    assert response.status_code == 401
    assert not WorkItem.objects.exists()


@override_settings(INTAKE_API_KEY="s3cret")
def test_intake_with_the_right_key_succeeds(client: APIClient) -> None:
    response = client.post(LIST_URL, PAYLOAD, format="json", headers={"X-API-Key": "s3cret"})

    assert response.status_code == 201


@override_settings(INTAKE_API_KEY="s3cret")
def test_the_key_only_guards_intake(client: APIClient) -> None:
    item = make_work_item(external_id="CRM-p")

    assert client.get(LIST_URL).status_code == 200
    assert client.get(f"{LIST_URL}/{item.id}").status_code == 200
