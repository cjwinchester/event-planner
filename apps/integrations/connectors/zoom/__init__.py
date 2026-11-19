"""Zoom connector: one meeting (or webinar) per session, hosted by the Zoom
user behind the session's virtual room, plus attendance counts afterwards.

The org's Owner/admin creates one Server-to-Server OAuth app (see
client.py for scopes) and enters its account ID, client ID and secret on the
org's Zoom IntegrationConfig. Each virtual room's `host_account` names the
licensed user (Member) whose account hosts that room's meetings.

Options (IntegrationConfig.options, non-secret):
  meeting_settings   dict merged into Zoom's `settings` for every meeting
                     (e.g. {"waiting_room": true}); account defaults apply
                     otherwise.
"""

from __future__ import annotations

from datetime import UTC
from urllib.parse import quote

from ...base import (
    AttendanceRecord,
    BaseConnector,
    Capability,
    ConnectionStatus,
    ConnectorError,
    CredentialField,
    MeetingInfo,
)
from ...registry import register
from .client import ZoomClient

SCHEDULED_MEETING, SCHEDULED_WEBINAR = 2, 5
LICENSED = 2  # Zoom user type: 1 Basic, 2 Licensed


def _client(ctx) -> ZoomClient:
    ctx.require("account_id", "client_id", "client_secret")
    return ZoomClient(
        account_id=ctx.get("account_id"), client_id=ctx.get("client_id"), client_secret=ctx.get("client_secret")
    )


def _user(host: str) -> str:
    """A Zoom user id/email as one path segment (a stray "/" or "?" can't reach other endpoints)."""
    return quote(host.strip(), safe="@")


def _plural(kind: str) -> str:
    return "webinars" if kind == "webinar" else "meetings"


@register
class ZoomConnector(BaseConnector):
    slug = "zoom"
    display_name = "Zoom"
    description = "Create a meeting or webinar for each virtual session and pull attendance counts."
    docs_url = "https://developers.zoom.us/docs/internal-apps/s2s-oauth/"
    capabilities = frozenset({Capability.VIRTUAL_SESSIONS})
    credential_fields = [
        CredentialField("account_id", "Account ID", secret=False),
        CredentialField("client_id", "Client ID", secret=False),
        CredentialField("client_secret", "Client secret"),
    ]

    def test_connection(self, ctx):
        try:
            me = _client(ctx).request("GET", "/users/me")
        except ConnectorError as exc:
            return ConnectionStatus(False, str(exc))
        return ConnectionStatus(True, f"Connected as {me.get('email', 'the account owner')}.")

    def check_host(self, ctx, *, host: str, kind: str) -> tuple[bool, str]:
        """Is `host` a licensed user in this account (with webinars, for a webinar room)?"""
        client = _client(ctx)
        try:
            user = client.request("GET", f"/users/{_user(host)}")
        except ConnectorError as exc:
            return False, f"Not found in this Zoom account ({exc})."
        if user.get("status") not in (None, "active"):
            return False, f"User is {user.get('status')}."
        if user.get("type") != LICENSED:
            return False, "User is Basic (unlicensed): meetings are capped at 40 minutes."
        if kind == "webinar":
            try:
                feature = client.request("GET", f"/users/{_user(host)}/settings").get("feature", {})
            except ConnectorError as exc:
                return False, f"Couldn't read the user's settings ({exc})."
            if not feature.get("webinar"):
                return False, "Webinar room, but this user has no webinar license."
            return True, "Licensed, webinars enabled."
        return True, "Licensed."

    def _body(self, ctx, *, kind, title, starts_at, minutes, recorded) -> dict:
        settings = {"auto_recording": "cloud" if recorded else "none", **ctx.options.get("meeting_settings", {})}
        return {
            "topic": title[:200],
            "type": SCHEDULED_WEBINAR if kind == "webinar" else SCHEDULED_MEETING,
            "start_time": starts_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration": minutes,
            "timezone": ctx.event.timezone if ctx.event else "UTC",
            "settings": settings,
        }

    def create_meeting(self, ctx, *, host, kind, title, starts_at, minutes, recorded):
        body = self._body(ctx, kind=kind, title=title, starts_at=starts_at, minutes=minutes, recorded=recorded)
        data = _client(ctx).request("POST", f"/users/{_user(host)}/{_plural(kind)}", json=body)
        # start_url (the host's key) is deliberately not kept: it expires and grants host control.
        return MeetingInfo(external_id=str(data["id"]), join_url=data["join_url"], raw={"uuid": data.get("uuid")})

    def update_meeting(self, ctx, *, external_id, kind, title, starts_at, minutes, recorded):
        body = self._body(ctx, kind=kind, title=title, starts_at=starts_at, minutes=minutes, recorded=recorded)
        _client(ctx).request("PATCH", f"/{_plural(kind)}/{external_id}", json=body)

    def delete_meeting(self, ctx, *, external_id, kind):
        _client(ctx).request("DELETE", f"/{_plural(kind)}/{external_id}", ok_missing=True)

    def fetch_attendance(self, ctx, *, external_id, kind):
        for row in _client(ctx).paged(f"/report/{_plural(kind)}/{external_id}/participants", "participants"):
            yield AttendanceRecord(
                participant_id=row.get("participant_user_id") or row.get("user_id") or "",
                email=row.get("user_email", ""),
                name=row.get("name", ""),
                minutes=(row.get("duration") or 0) // 60,
            )
