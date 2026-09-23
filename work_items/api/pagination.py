"""Pagination, shared by every API version.

The response names its own page so a client never has to parse a URL to know
where it is:

    {"count": 42, "page": 2, "pageSize": 20, "totalPages": 3, "results": [...]}
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class WorkItemPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "pageSize"
    max_page_size = 100

    def get_paginated_response(self, data: Any) -> Response:
        return Response(
            OrderedDict(
                [
                    ("count", self.page.paginator.count),
                    ("page", self.page.number),
                    ("pageSize", self.get_page_size(self.request)),
                    ("totalPages", self.page.paginator.num_pages),
                    ("results", data),
                ]
            )
        )

    def get_paginated_response_schema(self, schema: dict) -> dict:
        return {
            "type": "object",
            "required": ["count", "page", "pageSize", "totalPages", "results"],
            "properties": {
                "count": {"type": "integer", "example": 42},
                "page": {"type": "integer", "example": 1},
                "pageSize": {"type": "integer", "example": 20},
                "totalPages": {"type": "integer", "example": 3},
                "results": schema,
            },
        }
