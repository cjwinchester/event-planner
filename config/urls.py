"""Root URLconf.

- /admin/            Django admin — the staff UI for phase 1 (org-scoped)
- /accounts/         django-allauth (email login, Google Workspace SSO)
- /api/v1/           REST API (apps.api) — see /api/docs/ for the OpenAPI UI
- /feeds/            Stable, unauthenticated public feeds (ICS, Event Bundle JSON)
- /plan/             Staff planning UI (schedule grid, panels)
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("api/v1/", include("apps.api.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="api-schema"), name="api-docs"),
    path("feeds/", include("apps.publishing.urls")),
    path("plan/", include("apps.planner.urls")),
    path("", RedirectView.as_view(url="/plan/", permanent=False)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static("/published/", document_root=settings.STORAGES["publish"]["OPTIONS"]["location"])

admin.site.site_header = "Event Planner"
admin.site.site_title = "Event Planner admin"
admin.site.index_title = "Planning"
