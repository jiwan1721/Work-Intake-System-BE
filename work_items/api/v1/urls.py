"""v1 routes (PLAN §8.1).

`app_name = "v1"` is what makes DRF's NamespaceVersioning resolve
`request.version` to "v1". Tests reverse by namespace (`v1:work-item-list`)
rather than hard-coding paths, so the suite could later be parametrised over
versions without a search and replace.
"""

from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from .views import (
    AnalyseView,
    RetryView,
    StatusView,
    WorkItemDetailView,
    WorkItemListCreateView,
)

app_name = "v1"

urlpatterns = [
    path("work-items", WorkItemListCreateView.as_view(), name="work-item-list"),
    path("work-items/<uuid:pk>", WorkItemDetailView.as_view(), name="work-item-detail"),
    path("work-items/<uuid:pk>/analyse", AnalyseView.as_view(), name="work-item-analyse"),
    path("work-items/<uuid:pk>/retry", RetryView.as_view(), name="work-item-retry"),
    path("work-items/<uuid:pk>/status", StatusView.as_view(), name="work-item-status"),
    path("schema", SpectacularAPIView.as_view(), name="schema"),
    path("docs", SpectacularSwaggerView.as_view(url_name="v1:schema"), name="docs"),
]
