"""BE-10 / PLAN §10 #14: versioning, the error envelope and pagination."""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from work_items.domain.status import WorkItemStatus

from .factories import make_work_item

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def assert_envelope(body: dict, code: str) -> dict:
    """Every error, everywhere, has this shape (PLAN §8)."""
    assert set(body) == {"error"}
    error = body["error"]
    assert set(error) == {"code", "message", "details"}
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["details"], dict)
    return error


# --- Versioning ------------------------------------------------------------


def test_v1_is_routed_and_the_view_sees_its_version(client: APIClient) -> None:
    make_work_item(external_id="CRM-1")

    response = client.get("/api/v1/work-items")

    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_reversing_by_namespace_gives_the_v1_path() -> None:
    assert reverse("v1:work-item-list") == "/api/v1/work-items"


def test_request_version_is_v1(client: APIClient) -> None:
    """NamespaceVersioning is actually in play, not just a URL prefix."""
    from work_items.api.v1 import views

    seen: list[str | None] = []
    original = views.WorkItemListCreateView.get_queryset

    def capture(self):
        seen.append(self.request.version)
        return original(self)

    views.WorkItemListCreateView.get_queryset = capture
    try:
        client.get("/api/v1/work-items")
    finally:
        views.WorkItemListCreateView.get_queryset = original

    assert seen == ["v1"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/v2/work-items",  # a version that does not exist
        "/api/work-items",  # unversioned: no silent fallback to v1
        "/api/v1/nonsense",
    ],
)
def test_unknown_api_paths_404_with_the_json_envelope(client: APIClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 404
    assert response["Content-Type"].startswith("application/json")
    assert_envelope(response.json(), "NOT_FOUND")


def test_a_non_api_path_is_not_hijacked_by_the_json_handler(client: APIClient) -> None:
    response = client.get("/definitely-not-a-route")

    assert response.status_code == 404
    assert not response["Content-Type"].startswith("application/json")


# --- Error envelope --------------------------------------------------------


def test_400_uses_the_envelope_with_field_details(client: APIClient) -> None:
    response = client.post("/api/v1/work-items", {"title": ""}, format="json")

    assert response.status_code == 400
    error = assert_envelope(response.json(), "VALIDATION_ERROR")
    # Field names come back in the camelCase the client sent.
    assert "externalId" in error["details"]
    assert "title" in error["details"]


def test_404_for_an_unknown_item_uses_the_envelope(client: APIClient) -> None:
    response = client.get("/api/v1/work-items/0f9c6c1e-0000-4000-8000-000000000000")

    assert response.status_code == 404
    assert_envelope(response.json(), "NOT_FOUND")


def test_405_uses_the_envelope(client: APIClient) -> None:
    item = make_work_item(external_id="CRM-2")

    response = client.delete(f"/api/v1/work-items/{item.id}")

    assert response.status_code == 405
    assert_envelope(response.json(), "METHOD_NOT_ALLOWED")


def test_a_bug_in_a_view_returns_internal_error_without_a_stack_trace(
    client: APIClient, monkeypatch
) -> None:
    from work_items.api.v1 import views

    def explode(*args, **kwargs):
        raise RuntimeError("secret internal detail: db password is hunter2")

    monkeypatch.setattr(views, "analyse_work_item", explode)
    item = make_work_item(external_id="CRM-3")

    response = client.post(f"/api/v1/work-items/{item.id}/analyse", raise_request_exception=False)

    assert response.status_code == 500
    error = assert_envelope(response.json(), "INTERNAL_ERROR")
    assert "hunter2" not in error["message"]
    assert "Traceback" not in error["message"]


# --- Pagination ------------------------------------------------------------


def test_list_is_paginated_and_names_its_page(client: APIClient) -> None:
    for index in range(25):
        make_work_item(external_id=f"CRM-p{index}")

    response = client.get("/api/v1/work-items")
    body = response.json()

    assert body["count"] == 25
    assert body["page"] == 1
    assert body["pageSize"] == 20
    assert body["totalPages"] == 2
    assert len(body["results"]) == 20


def test_page_size_can_be_set_and_is_capped(client: APIClient) -> None:
    for index in range(5):
        make_work_item(external_id=f"CRM-q{index}")

    assert client.get("/api/v1/work-items?pageSize=2").json()["pageSize"] == 2
    # Above max_page_size the server clamps rather than obeying.
    assert client.get("/api/v1/work-items?pageSize=5000").json()["pageSize"] == 100


def test_second_page_returns_the_remainder(client: APIClient) -> None:
    for index in range(22):
        make_work_item(external_id=f"CRM-r{index}")

    body = client.get("/api/v1/work-items?page=2").json()

    assert body["page"] == 2
    assert len(body["results"]) == 2


def test_an_out_of_range_page_is_a_404_envelope(client: APIClient) -> None:
    make_work_item(external_id="CRM-4")

    response = client.get("/api/v1/work-items?page=99")

    assert response.status_code == 404
    assert_envelope(response.json(), "NOT_FOUND")


def test_items_come_back_newest_first(client: APIClient) -> None:
    older = make_work_item(external_id="CRM-old")
    newer = make_work_item(external_id="CRM-new")

    results = client.get("/api/v1/work-items").json()["results"]

    assert [row["externalId"] for row in results] == [newer.external_id, older.external_id]


# --- OpenAPI (BE-13) -------------------------------------------------------


def test_the_schema_is_served_and_describes_the_endpoints(client: APIClient) -> None:
    response = client.get("/api/v1/schema")

    assert response.status_code == 200
    schema = response.content.decode()
    assert "/api/v1/work-items" in schema
    assert "/api/v1/work-items/{id}/analyse" in schema
    assert "/api/v1/work-items/{id}/retry" in schema
    assert "/api/v1/work-items/{id}/status" in schema


def test_swagger_ui_is_served(client: APIClient) -> None:
    assert client.get("/api/v1/docs").status_code == 200


def test_every_status_value_appears_in_the_schema(client: APIClient) -> None:
    schema = client.get("/api/v1/schema").content.decode()

    for status in WorkItemStatus:
        assert status.value in schema
