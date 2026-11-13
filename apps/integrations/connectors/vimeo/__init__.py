"""Vimeo connector — partial.

Personal access token auth. `list_videos` works (GET /me/videos or a
folder's /me/projects/{id}/videos, paged). Planned: attach recordings to
sessions by matching titles/IDs (ire-data-dashboard's connectors/vimeo has
a fuller client to port) and upload/replace for stream archives.
"""

import requests

from ...base import BaseConnector, Capability, ConnectionStatus, ConnectorError, CredentialField
from ...registry import register

API = "https://api.vimeo.com"


@register
class VimeoConnector(BaseConnector):
    slug = "vimeo"
    display_name = "Vimeo"
    description = "Session recordings: list videos and attach recording links."
    docs_url = "https://developer.vimeo.com/api/reference"
    capabilities = frozenset({Capability.VIDEO_HOSTING})
    credential_fields = [CredentialField("access_token", "Personal access token")]

    def _get(self, ctx, path, **params):
        ctx.require("access_token")
        resp = requests.get(
            f"{API}{path}",
            params=params,
            headers={
                "Authorization": f"bearer {ctx.get('access_token')}",
                "Accept": "application/vnd.vimeo.*+json;version=3.4",
            },
            timeout=30,
        )
        if not resp.ok:
            raise ConnectorError(f"Vimeo {path} failed ({resp.status_code})")
        return resp.json()

    def test_connection(self, ctx):
        try:
            me = self._get(ctx, "/me")
        except ConnectorError as exc:
            return ConnectionStatus(False, str(exc))
        return ConnectionStatus(True, f"Connected as {me.get('name', '?')}.")

    def list_videos(self, ctx, *, folder=""):
        path = f"/me/projects/{folder}/videos" if folder else "/me/videos"
        page = 1
        while True:
            data = self._get(ctx, path, page=page, per_page=100, fields="uri,name,link,duration,created_time")
            yield from data.get("data", [])
            if not data.get("paging", {}).get("next"):
                return
            page += 1
