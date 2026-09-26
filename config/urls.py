"""Root URL configuration.

Only the API layer is versioned (PLAN §8.1): every client-facing route lives
under `/api/v1/`. A future v2 mounts its own package here and reuses the same
services.

Unknown versions fail loudly. `/api/v2/...` and an unversioned
`/api/work-items` both 404 — with the JSON error envelope, so a client that
calls the wrong version gets the same shape as every other error rather than
an HTML page.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/auth/", include("users.api.v1.urls")),
    path("api/v1/", include("work_items.api.v1.urls")),
    # future: path("api/v2/", include("work_items.api.v2.urls")),
]

handler404 = "work_items.api.exceptions.api_not_found"
