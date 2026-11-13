"""Zoom connector — STUB.

Plan: Server-to-Server OAuth app (account_id + client_id + client_secret →
POST https://zoom.us/oauth/token?grant_type=account_credentials&account_id=...).
  create_meeting  → POST /v2/users/{host}/webinars (or /meetings) with
                    start_time (UTC), duration, topic → MeetingInfo(join_url)
  fetch_attendance→ GET /v2/report/webinars/{id}/participants (paged)
Per-event credential: `host_user` (which Zoom user/room hosts the event),
which maps naturally to venues.Room.host_account for virtual rooms.
Wire results back with integrations.services.attach_meeting (Session.live_url
+ ExternalIdentity).
"""

from ...base import (
    BaseConnector,
    Capability,
    ConnectionStatus,
    CredentialField,
    NotImplementedYet,
    Scope,
)
from ...registry import register


@register
class ZoomConnector(BaseConnector):
    slug = "zoom"
    display_name = "Zoom"
    description = "Create meetings/webinars for sessions and pull attendance (stub)."
    docs_url = "https://developers.zoom.us/docs/api/"
    capabilities = frozenset({Capability.VIRTUAL_SESSIONS})
    credential_fields = [
        CredentialField("account_id", "Account ID", secret=False),
        CredentialField("client_id", "Client ID", secret=False),
        CredentialField("client_secret", "Client secret"),
        CredentialField("host_user", "Default host user/email", secret=False, required=False, scope=Scope.EVENT),
    ]

    def test_connection(self, ctx):
        return ConnectionStatus(False, "Zoom connector is a stub.")

    def create_meeting(self, ctx, *, title, starts_at, minutes):
        raise NotImplementedYet("Zoom meeting creation not implemented yet")

    def fetch_attendance(self, ctx, *, external_id):
        raise NotImplementedYet("Zoom attendance not implemented yet")
