"""MemberClicks connector — STUB.

Plan: MemberClicks' REST API uses OAuth2 client-credentials
(POST https://<org>.memberclicks.net/oauth/v1/token, grant_type=client_credentials,
Basic auth with client id/secret). Profiles are searched via
POST /api/v1/profile/search (a search returns an id; GET the results page
by page). Map profile fields → MemberRecord in `_profile_to_member`.
The earlier conference-planning-dashboard attempt has a token-fetch sketch
(apps/integrations/services/memberclicks/integration.py).
"""

from ...base import (
    BaseConnector,
    Capability,
    ConnectionStatus,
    CredentialField,
    NotImplementedYet,
)
from ...registry import register


@register
class MemberClicksConnector(BaseConnector):
    slug = "memberclicks"
    display_name = "MemberClicks"
    description = "AMS: member lookup and membership sync (stub)."
    docs_url = "https://help.memberclicks.com/hc/en-us/articles/230536287-API-Overview"
    capabilities = frozenset({Capability.MEMBER_LOOKUP, Capability.MEMBERSHIP_SYNC})
    credential_fields = [
        CredentialField("org_id", "MemberClicks org ID (subdomain)", secret=False),
        CredentialField("client_id", "OAuth client ID", secret=False),
        CredentialField("client_secret", "OAuth client secret"),
    ]

    def test_connection(self, ctx):
        return ConnectionStatus(False, "MemberClicks connector is a stub.")

    def find_member(self, ctx, *, email):
        raise NotImplementedYet("MemberClicks member lookup not implemented yet")

    def fetch_members(self, ctx):
        raise NotImplementedYet("MemberClicks membership sync not implemented yet")
