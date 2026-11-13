"""MemberSuite connector: member lookup, membership sync, event registrations.

MSQL field names and JSON-key casing rules come from ire-data-dashboard
(connectors/membersuite/connector.py): MSQL answers with each column's
first character lowercased ("membership.Type.Name"), and dotted names work
for scalar lookups used here.

UNVERIFIED (needs a live tenant): the `Individual.PrimaryAddress_Address_*`
columns on EventRegistration. The dashboard found bulk MSQL silently drops
some dotted names (phones); if city/region come back empty, fetch addresses
from Individual by ID instead. Registrations still sync without them — they
only feed the attendee heat map.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from ...base import (
    BaseConnector,
    Capability,
    ConnectionStatus,
    ConnectorContext,
    ConnectorError,
    CredentialField,
    MemberRecord,
    RegistrationRecord,
)
from ...registry import register
from .client import MemberSuiteClient

_MEMBER_FIELDS = [
    "ID", "FirstName", "LastName", "EmailAddress", "PrimaryOrganizationOrCompanyName",
    "Membership.Type.Name", "Membership.ExpirationDate", "Membership.ReceivesMemberBenefits",
]  # fmt: skip

_REGISTRATION_FIELDS = [
    "ID", "Individual.ID", "Individual.FirstName", "Individual.LastName", "Individual.EmailAddress",
    "Individual.PrimaryOrganizationOrCompanyName", "CreatedDate", "CancellationDate", "Fee.Name",
    "Individual.PrimaryAddress_Address_City", "Individual.PrimaryAddress_Address_State",
    "Individual.PrimaryAddress_Address_PostalCode", "Individual.PrimaryAddress_Address_Country",
]  # fmt: skip


def _dt(value) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _quote(value: str) -> str:
    return value.replace("'", "''")


def _row_to_member(row: dict, client: MemberSuiteClient) -> MemberRecord:
    expires = _dt(row.get("membership.ExpirationDate"))
    return MemberRecord(
        external_id=row["id"],
        first_name=row.get("firstName") or "",
        last_name=row.get("lastName") or "",
        email=row.get("emailAddress") or "",
        organization_name=row.get("primaryOrganizationOrCompanyName") or "",
        membership_type=row.get("membership.Type.Name") or "",
        membership_expires=expires,
        is_active_member=bool(row.get("membership.ReceivesMemberBenefits")),
        raw=row,
    )


@register
class MemberSuiteConnector(BaseConnector):
    slug = "membersuite"
    display_name = "MemberSuite"
    description = "AMS: look up members, sync memberships, pull event registrations."
    docs_url = "https://rest.membersuite.com"
    capabilities = frozenset({Capability.MEMBER_LOOKUP, Capability.MEMBERSHIP_SYNC, Capability.REGISTRATION_SYNC})
    credential_fields = [
        CredentialField("username", "API username", secret=False),
        CredentialField("password", "API password"),
        CredentialField("tenant_id", "Tenant ID", secret=False),
        CredentialField("association_id", "Association ID (for photo URLs)", secret=False, required=False),
    ]

    def client(self, ctx: ConnectorContext) -> MemberSuiteClient:
        return MemberSuiteClient(
            username=ctx.get("username", ""),
            password=ctx.get("password", ""),
            tenant_id=ctx.get("tenant_id", ""),
            association_id=ctx.get("association_id", ""),
        )

    def test_connection(self, ctx):
        try:
            self.client(ctx).token()
        except ConnectorError as exc:
            return ConnectionStatus(False, str(exc))
        return ConnectionStatus(True, "Authenticated.")

    def find_member(self, ctx: ConnectorContext, *, email: str) -> MemberRecord | None:
        client = self.client(ctx)
        msql = f"SELECT {', '.join(_MEMBER_FIELDS)} FROM Individual WHERE EmailAddress = '{_quote(email)}'"
        rows = client.execute_msql(msql, page_size=5)
        return _row_to_member(rows[0], client) if rows else None

    def fetch_members(self, ctx: ConnectorContext) -> Iterable[MemberRecord]:
        client = self.client(ctx)
        msql = (
            f"SELECT {', '.join(_MEMBER_FIELDS)} FROM Individual "
            "WHERE Membership.ReceivesMemberBenefits = 1 ORDER BY SortName"
        )
        for row in client.iter_msql(msql):
            yield _row_to_member(row, client)

    def fetch_registrations(self, ctx: ConnectorContext, *, external_event_id: str) -> Iterable[RegistrationRecord]:
        client = self.client(ctx)
        msql = (
            f"SELECT {', '.join(_REGISTRATION_FIELDS)} FROM EventRegistration "
            f"WHERE (Event.ID = '{_quote(external_event_id)}') ORDER BY CreatedDate"
        )
        for row in client.iter_msql(msql):
            created = _dt(row.get("createdDate"))
            if created is None:
                continue
            yield RegistrationRecord(
                external_id=row["id"],
                registered_at=created,
                canceled_at=_dt(row.get("cancellationDate")),
                first_name=row.get("individual.FirstName") or "",
                last_name=row.get("individual.LastName") or "",
                email=row.get("individual.EmailAddress") or "",
                affiliation=row.get("individual.PrimaryOrganizationOrCompanyName") or "",
                ticket_type=row.get("fee.Name") or "",
                city=row.get("individual.PrimaryAddress_Address_City") or "",
                region=row.get("individual.PrimaryAddress_Address_State") or "",
                postal_code=row.get("individual.PrimaryAddress_Address_PostalCode") or "",
                country=(row.get("individual.PrimaryAddress_Address_Country") or "")[:2],
                member_external_id=row.get("individual.ID") or "",
                raw=row,
            )
