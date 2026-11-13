"""Connector framework.

A connector is a small class that knows how to talk to one outside system.
It declares:

  slug / display_name / description / docs_url
  credential_fields  — what an org admin must enter, each scoped ORG or EVENT
                       (an AMS login is org-wide; a webinar series ID is
                       per-event — design borrowed from the earlier
                       conference-planning-dashboard attempt)
  capabilities       — which capability protocols it implements

Capabilities are small typed protocols (below). Services ask the registry
for "the enabled connector with capability X for this org" instead of
importing a specific vendor — that is what makes integrations toggleable
per organization and lets a MemberClicks org and a MemberSuite org share
every code path above the connector.

Connectors receive a `ConnectorContext` (org, optional event, decrypted
credentials, non-secret options). They must NOT touch the ORM directly for
their own state; they return normalized dataclasses and the integration
services (integrations.services) upsert them. That keeps connectors
testable with plain dicts and reusable outside Django.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class Scope(StrEnum):
    ORG = "org"
    EVENT = "event"


class Capability(StrEnum):
    MEMBER_LOOKUP = "member_lookup"  # find/verify a person (link speakers to member records)
    MEMBERSHIP_SYNC = "membership_sync"  # list active members
    REGISTRATION_SYNC = "registration_sync"  # attendee registrations for an event
    VIRTUAL_SESSIONS = "virtual_sessions"  # create meetings/webinars, fetch attendance
    VIDEO_HOSTING = "video_hosting"  # recordings (upload/list/attach)
    STORAGE = "storage"  # publish files (static schedule exports, media)


@dataclasses.dataclass
class CredentialField:
    key: str
    label: str
    help_text: str = ""
    secret: bool = True
    required: bool = True
    scope: Scope = Scope.ORG


@dataclasses.dataclass
class ConnectorContext:
    organization: Any
    event: Any = None
    credentials: dict = dataclasses.field(default_factory=dict)
    options: dict = dataclasses.field(default_factory=dict)

    def require(self, *keys: str) -> None:
        missing = [k for k in keys if not self.credentials.get(k) and not self.options.get(k)]
        if missing:
            raise ConnectorConfigError(f"Missing settings: {', '.join(missing)}")

    def get(self, key: str, default=None):
        return self.credentials.get(key, self.options.get(key, default))


@dataclasses.dataclass
class ConnectionStatus:
    ok: bool
    message: str = ""


class ConnectorError(Exception):
    """A connector call failed (network, auth, API error)."""


class ConnectorConfigError(ConnectorError):
    """Missing/invalid credentials or options."""


class NotImplementedYet(ConnectorError):
    """Stubbed connector method — see the connector's docstring for the plan."""


# --- normalized records connectors return ---------------------------------


@dataclasses.dataclass
class MemberRecord:
    external_id: str
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    organization_name: str = ""
    membership_type: str = ""
    membership_expires: datetime | None = None
    is_active_member: bool = False
    external_url: str = ""
    raw: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class RegistrationRecord:
    external_id: str
    registered_at: datetime
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    affiliation: str = ""
    ticket_type: str = ""
    amount_paid: float | None = None
    canceled_at: datetime | None = None
    member_status: str = ""
    city: str = ""
    region: str = ""
    postal_code: str = ""
    country: str = ""
    member_external_id: str = ""
    raw: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class MeetingInfo:
    external_id: str
    join_url: str
    host_url: str = ""
    raw: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class AttendanceRecord:
    email: str
    name: str = ""
    joined_at: datetime | None = None
    left_at: datetime | None = None
    minutes: int | None = None


# --- capability protocols ---------------------------------------------------


@runtime_checkable
class MemberLookup(Protocol):
    def find_member(self, ctx: ConnectorContext, *, email: str) -> MemberRecord | None: ...


@runtime_checkable
class MembershipSync(Protocol):
    def fetch_members(self, ctx: ConnectorContext) -> Iterable[MemberRecord]: ...


@runtime_checkable
class RegistrationSync(Protocol):
    def fetch_registrations(self, ctx: ConnectorContext, *, external_event_id: str) -> Iterable[RegistrationRecord]: ...


@runtime_checkable
class VirtualSessions(Protocol):
    def create_meeting(
        self, ctx: ConnectorContext, *, title: str, starts_at: datetime, minutes: int
    ) -> MeetingInfo: ...

    def fetch_attendance(self, ctx: ConnectorContext, *, external_id: str) -> Iterable[AttendanceRecord]: ...


@runtime_checkable
class VideoHosting(Protocol):
    def list_videos(self, ctx: ConnectorContext, *, folder: str = "") -> Iterable[dict]: ...


@runtime_checkable
class Storage(Protocol):
    def base_url(self, ctx: ConnectorContext, prefix: str) -> str: ...

    def put(self, ctx: ConnectorContext, key: str, content: bytes, content_type: str) -> str: ...


CAPABILITY_PROTOCOLS = {
    Capability.MEMBER_LOOKUP: MemberLookup,
    Capability.MEMBERSHIP_SYNC: MembershipSync,
    Capability.REGISTRATION_SYNC: RegistrationSync,
    Capability.VIRTUAL_SESSIONS: VirtualSessions,
    Capability.VIDEO_HOSTING: VideoHosting,
    Capability.STORAGE: Storage,
}


class BaseConnector:
    slug: str = ""
    display_name: str = ""
    description: str = ""
    docs_url: str = ""
    credential_fields: list[CredentialField] = []
    capabilities: frozenset[Capability] = frozenset()

    def test_connection(self, ctx: ConnectorContext) -> ConnectionStatus:
        """Cheap authenticated call proving the credentials work."""
        return ConnectionStatus(ok=False, message="Connection test not implemented for this connector.")

    def fields_for(self, scope: Scope) -> list[CredentialField]:
        return [f for f in self.credential_fields if f.scope == scope]
