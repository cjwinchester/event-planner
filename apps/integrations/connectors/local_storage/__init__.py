"""Local filesystem storage (Django's "publish" storage) — the default
publish target when an organization has no S3 integration. Dev/testing,
or small self-hosted installs serving /published/ from the web server."""

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages

from ...base import BaseConnector, Capability, ConnectionStatus
from ...registry import register


@register
class LocalStorageConnector(BaseConnector):
    slug = "local"
    display_name = "Local storage"
    description = "Write published files to this server's publish directory."
    capabilities = frozenset({Capability.STORAGE})

    def _storage(self):
        return storages["publish"]

    def base_url(self, ctx, prefix):
        # Absolute URLs, so feeds that cross-link (schedule_formats, ICS) work
        # outside this server. Set LOCAL_PUBLISH_BASE_URL to the public origin.
        base = settings.EVENT_PLANNER.get("LOCAL_PUBLISH_BASE_URL") or self._storage().url("")
        return base.rstrip("/") + "/" + prefix

    def put(self, ctx, key, content, content_type):
        storage = self._storage()
        if storage.exists(key):
            storage.delete(key)
        storage.save(key, ContentFile(content))
        return key

    def test_connection(self, ctx):
        return ConnectionStatus(True, "Local storage is always available.")
