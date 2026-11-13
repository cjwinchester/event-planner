"""MemberSuite REST client — ported from ire-data-dashboard's
connectors/membersuite/api.py, made multi-tenant:

  * credentials come from the org's IntegrationConfig, not env vars;
  * the token cache is Django's cache keyed per tenant (not a file in ~);
  * one MemberSuiteClient per (org) context.

Behavior kept from the original (hard-won):
  * full auth via msc_authdata, then refresh-token exchange, re-auth on 401
    exactly once ("_reauth_and_retry_on_401");
  * a failed MSQL page RAISES — never return [] on error, because every
    pagination loop treats an empty page as "done" and would silently
    truncate a sync;
  * a 1-second pause between calls (MemberSuite rate limits).
"""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Iterator

import requests
from django.core.cache import cache

from ...base import ConnectorConfigError, ConnectorError

logger = logging.getLogger(__name__)

BASE_URL = "https://rest.membersuite.com/platform/v2"
PAUSE_SECONDS = 1.0


def _jwt_expiry(token: str) -> int:
    raw = token.removeprefix("Bearer ")
    try:
        payload = raw.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return int(json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0))
    except Exception:
        return 0


class MemberSuiteClient:
    def __init__(self, *, username: str, password: str, tenant_id: str, association_id: str = "", pause=PAUSE_SECONDS):
        if not (username and password and tenant_id):
            raise ConnectorConfigError("MemberSuite needs username, password and tenant_id")
        self.username, self.password, self.tenant_id = username, password, tenant_id
        self.association_id = association_id
        self.pause = pause
        self._cache_key = f"membersuite-token:{tenant_id}:{username}"

    # --- auth -------------------------------------------------------------
    def _store(self, access: str, refresh: str | None) -> str:
        expires = _jwt_expiry(access)
        ttl = max(int(expires - time.time()), 60) if expires else 3600
        cache.set(self._cache_key, {"access": access, "refresh": refresh, "expires": expires}, ttl + 86400)
        return access

    def _full_auth(self) -> str:
        resp = requests.post(
            f"{BASE_URL}/msc_authdata/{self.tenant_id}",
            json={"username": self.username, "password": self.password},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        if not resp.ok:
            raise ConnectorError(f"MemberSuite auth failed ({resp.status_code})")
        data = resp.json()
        return self._store(f"Bearer {data['accessToken']}", data.get("refreshToken"))

    def _refresh(self, refresh_token: str) -> str | None:
        resp = requests.get(f"{BASE_URL}/refreshtoken", params={"refreshToken": refresh_token}, timeout=30)
        if not resp.ok or not resp.json().get("success"):
            return None
        payload = resp.json().get("data", {})
        if isinstance(payload, str):
            access = payload if payload.startswith("Bearer ") else f"Bearer {payload}"
            return self._store(access, refresh_token)
        return self._store(f"Bearer {payload['accessToken']}", payload.get("refreshToken") or refresh_token)

    def token(self) -> str:
        cached = cache.get(self._cache_key) or {}
        if cached.get("access") and time.time() < cached.get("expires", 0) - 60:
            return cached["access"]
        if cached.get("refresh"):
            refreshed = self._refresh(cached["refresh"])
            if refreshed:
                return refreshed
        return self._full_auth()

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        def call(tok):
            headers = {"Authorization": tok, "Accept": "application/json"}
            return requests.request(method, url, headers=headers, timeout=60, **kwargs)

        resp = call(self.token())
        if resp.status_code == 401:
            logger.warning("MemberSuite rejected cached token — re-authenticating once")
            resp = call(self._full_auth())
        if self.pause:
            time.sleep(self.pause)
        return resp

    # --- data ---------------------------------------------------------------
    def execute_msql(self, msql: str, *, start_position: int = 0, page_size: int = 10000) -> list[dict]:
        resp = self._request(
            "POST",
            f"{BASE_URL}/dataSuite/executeSearch",
            json={"msql": msql},
            params={"startPosition": start_position, "pageSize": page_size},
        )
        if resp.status_code != 200:
            raise ConnectorError(f"MSQL search failed - {resp.status_code}: {resp.text[:500]}")
        result = resp.json()
        if not result.get("success"):
            raise ConnectorError(f"MSQL error: {str(result)[:500]}")
        return result.get("data", [])

    def iter_msql(self, msql: str, page_size: int = 10000) -> Iterator[dict]:
        start = 0
        while True:
            page = self.execute_msql(msql, start_position=start, page_size=page_size)
            yield from page
            if len(page) < page_size:
                return
            start += page_size

    def get_object(self, object_id: str) -> dict | None:
        resp = self._request("GET", f"{BASE_URL}/dataSuite/get/{object_id}")
        if not resp.ok:
            raise ConnectorError(f"MemberSuite object fetch failed - {resp.status_code}")
        result = resp.json()
        if not result.get("success"):
            raise ConnectorError(f"MemberSuite error: {str(result)[:500]}")
        return result.get("data")

    def image_url(self, image_id: str | None) -> str:
        if not (image_id and self.association_id):
            return ""
        return f"https://images.membersuite.com/{self.association_id}/{self.tenant_id}/{image_id}"
