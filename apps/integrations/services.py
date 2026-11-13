"""Glue between connectors (pure API clients) and the database.

context_for(org, slug, event=None)      → ConnectorContext (or raise if disabled)
enabled_connector(org, capability)      → (connector, ctx) for the org's enabled provider
sync_registrations(event, triggered_by) → SyncRun (upserts events.Registration)
link_speaker_to_member(speaker, org)    → ExternalIdentity | None
"""

from __future__ import annotations

import logging

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from apps.events.models import Registration

from .base import Capability, ConnectorConfigError, ConnectorContext, ConnectorError
from .models import ExternalIdentity, IntegrationConfig, SyncRun
from .registry import connectors_with, get_connector

logger = logging.getLogger(__name__)


def context_for(organization, slug: str, event=None) -> ConnectorContext:
    found = IntegrationConfig.get_credentials(organization, slug, event=event)
    if found is None:
        raise ConnectorConfigError(f"{slug} is not enabled for {organization}")
    credentials, options = found
    return ConnectorContext(organization=organization, event=event, credentials=credentials, options=options)


def enabled_connector(organization, capability: Capability, event=None, *, prefer: str = ""):
    """The first enabled connector with `capability` for this org (`prefer` wins if enabled)."""
    candidates = connectors_with(capability)
    if prefer:
        candidates.sort(key=lambda c: c.slug != prefer)
    for connector in candidates:
        try:
            return connector, context_for(organization, connector.slug, event)
        except ConnectorConfigError:
            continue
    return None, None


def test_integration(config: IntegrationConfig):
    connector = get_connector(config.connector)
    ctx = context_for(config.organization, config.connector, config.event)
    status = connector.test_connection(ctx)
    config.last_tested_at = timezone.now()
    config.last_test_ok = status.ok
    config.last_test_message = status.message
    config.save(update_fields=["last_tested_at", "last_test_ok", "last_test_message", "updated_at"])
    return status


def _external_event_id(event, connector_slug: str) -> str:
    ct = ContentType.objects.get_for_model(event)
    ident = ExternalIdentity.objects.filter(
        organization=event.organization, connector=connector_slug, content_type=ct, object_id=event.pk
    ).first()
    return ident.external_id if ident else ""


def sync_registrations(event, triggered_by=None) -> SyncRun:
    """Pull registrations for `event` from its registration connector.

    The event must name its connector (`Event.registration_connector`) and
    have an ExternalIdentity giving the external event ID.
    """
    slug = event.registration_connector
    run = SyncRun.objects.create(
        organization=event.organization,
        event=event,
        connector=slug or "?",
        operation="sync_registrations",
        status=SyncRun.Status.RUNNING,
        started_at=timezone.now(),
        triggered_by=triggered_by,
    )
    stats = {"created": 0, "updated": 0}
    try:
        if not slug:
            raise ConnectorConfigError("Event has no registration_connector set.")
        connector = get_connector(slug)
        if Capability.REGISTRATION_SYNC not in connector.capabilities:
            raise ConnectorConfigError(f"{slug} can't sync registrations.")
        external_id = _external_event_id(event, slug)
        if not external_id:
            raise ConnectorConfigError(f"Link this event to its {slug} event ID first (External identities).")
        ctx = context_for(event.organization, slug, event)
        with transaction.atomic():
            for rec in connector.fetch_registrations(ctx, external_event_id=external_id):
                _, created = Registration.objects.update_or_create(
                    event=event,
                    source=slug,
                    external_id=rec.external_id,
                    defaults={
                        "first_name": rec.first_name,
                        "last_name": rec.last_name,
                        "email": rec.email,
                        "affiliation": rec.affiliation,
                        "ticket_type": rec.ticket_type,
                        "amount_paid": rec.amount_paid,
                        "registered_at": rec.registered_at,
                        "canceled_at": rec.canceled_at,
                        "member_status": rec.member_status,
                        "city": rec.city,
                        "region": rec.region,
                        "postal_code": rec.postal_code,
                        "country": rec.country,
                        "raw": rec.raw,
                    },
                )
                stats["created" if created else "updated"] += 1
        run.status = SyncRun.Status.SUCCEEDED
    except ConnectorError as exc:
        run.status = SyncRun.Status.FAILED
        run.message = str(exc)
        logger.warning("Registration sync failed for %s: %s", event, exc)
    run.stats = stats
    run.finished_at = timezone.now()
    run.save()
    return run


def link_speaker_to_member(speaker, organization):
    """Find the speaker in the org's AMS by email and record the link.
    Requires the speaker to have shared private details with the org."""
    from apps.speakers.sharing import can_view_private

    if not speaker.email or not can_view_private(organization, speaker):
        return None
    connector, ctx = enabled_connector(organization, Capability.MEMBER_LOOKUP)
    if connector is None:
        return None
    member = connector.find_member(ctx, email=speaker.email)
    if member is None:
        return None
    ident, _ = ExternalIdentity.objects.update_or_create(
        organization=organization,
        connector=connector.slug,
        content_type=ContentType.objects.get_for_model(speaker),
        external_id=member.external_id,
        defaults={"object_id": speaker.pk, "data": member.raw, "last_synced_at": timezone.now()},
    )
    return ident
