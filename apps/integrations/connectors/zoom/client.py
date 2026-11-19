"""Zoom REST client (Server-to-Server OAuth).

One S2S OAuth app, created by the account Owner/admin in the Zoom App
Marketplace, can schedule meetings for every licensed user (Member) in the
account: `POST /users/{email}/meetings`. Scopes the app needs:

  meeting:write:meeting:admin, meeting:update:meeting:admin,
  meeting:delete:meeting:admin, webinar:write:webinar:admin,
  webinar:update:webinar:admin, webinar:delete:webinar:admin,
  report:read:list_meeting_participants:admin,
  report:read:list_webinar_participants:admin,
  user:read:user:admin, user:read:settings:admin   (setup test: hosts exist, licensed, webinars)

Tokens last an hour and are cached per account in Django's cache. Zoom
limits meeting create/update to 100 requests per user per day, far above a
conference's sessions per room.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import requests
from django.core.cache import cache

from ...base import ConnectorConfigError, ConnectorError

logger = logging.getLogger(__name__)

API = "https://api.zoom.us/v2"
TOKEN_URL = "https://zoom.us/oauth/token"


class ZoomClient:
    def __init__(self, *, account_id: str, client_id: str, client_secret: str, timeout: int = 30):
        if not (account_id and client_id and client_secret):
            raise ConnectorConfigError("Zoom needs account_id, client_id and client_secret")
        self.account_id, self.client_id, self.client_secret = account_id, client_id, client_secret
        self.timeout = timeout
        self._cache_key = f"zoom-token:{account_id}:{client_id}"

    def _token(self, *, fresh: bool = False) -> str:
        token = None if fresh else cache.get(self._cache_key)
        if token:
            return token
        resp = requests.post(
            TOKEN_URL,
            params={"grant_type": "account_credentials", "account_id": self.account_id},
            auth=(self.client_id, self.client_secret),
            timeout=self.timeout,
        )
        if not resp.ok:
            raise ConnectorError(f"Zoom auth failed ({resp.status_code}): {_reason(resp)}")
        data = resp.json()
        token = data["access_token"]
        cache.set(self._cache_key, token, max(int(data.get("expires_in", 3600)) - 120, 60))
        return token

    def request(self, method: str, path: str, *, ok_missing: bool = False, **kwargs) -> dict:
        """Call the API; re-authenticate once on 401. `ok_missing` treats 404 as
        success (deleting a meeting someone already removed in Zoom)."""
        for attempt in (0, 1):
            resp = requests.request(
                method,
                f"{API}{path}",
                headers={"Authorization": f"Bearer {self._token(fresh=attempt == 1)}"},
                timeout=self.timeout,
                **kwargs,
            )
            if resp.status_code == 401 and attempt == 0:
                continue
            break
        if ok_missing and resp.status_code == 404:
            return {}
        if not resp.ok:
            raise ConnectorError(f"Zoom {method} {path} failed ({resp.status_code}): {_reason(resp)}")
        return resp.json() if resp.content else {}

    def paged(self, path: str, key: str, *, params: dict | None = None) -> Iterator[dict]:
        params = {"page_size": 300, **(params or {})}
        while True:
            data = self.request("GET", path, params=params)
            yield from data.get(key, [])
            token = data.get("next_page_token")
            if not token:
                return
            params["next_page_token"] = token


def _reason(resp) -> str:
    try:
        return resp.json().get("message") or resp.text[:200]
    except ValueError:
        return resp.text[:200]
