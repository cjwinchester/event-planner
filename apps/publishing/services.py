"""Publish an event: bundle → exporters → storage target → PublishRun.

    publish_event(event, formats=None, user=None, dry_run=False) -> PublishRun

Target: the org's enabled storage connector (S3 if configured), otherwise
local storage. Paths come from the connector option `path_template`
(default "{org}/{event}/"), so URLs stay stable across republishes —
calendar subscriptions and attendee favorites depend on that.
Each format's URL is computed *before* rendering so feeds can cross-link
(the schedule-ui JSON lists every format; the HTML bundle points at its JSON).
"""

from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.utils import timezone

from apps.integrations.base import Capability, ConnectorContext
from apps.integrations.registry import get_connector
from apps.integrations.services import enabled_connector

from .bundle import Audience, build_event_bundle, validate_bundle
from .exporters import ExportContext, default_formats, get_exporter
from .models import PublishRun

logger = logging.getLogger(__name__)

DEFAULT_PATH_TEMPLATE = "{org}/{event}/"


def storage_target(event):
    """(connector, ctx, prefix) for where this event publishes."""
    connector, ctx = enabled_connector(event.organization, Capability.STORAGE, event, prefer="s3")
    if connector is None:
        connector = get_connector("local")
        ctx = ConnectorContext(organization=event.organization, event=event)
    template = ctx.get("path_template") or DEFAULT_PATH_TEMPLATE
    prefix = template.format(org=event.organization.slug, event=event.slug)
    return connector, ctx, prefix


def feed_urls(event, formats=None, bundle: dict | None = None) -> dict[str, str]:
    connector, ctx, prefix = storage_target(event)
    base = connector.base_url(ctx, prefix)
    bundle = bundle or {"event": {"slug": event.slug}}
    urls = {}
    for slug in formats or default_formats():
        for name, path in get_exporter(slug).filenames(bundle).items():
            urls[name] = base + path
    return urls


def publish_event(event, formats: list[str] | None = None, user=None, dry_run: bool = False) -> PublishRun:
    formats = formats or default_formats()
    connector, ctx, prefix = storage_target(event)
    run = PublishRun.objects.create(
        event=event,
        status=PublishRun.Status.RUNNING,
        target=connector.slug,
        formats=formats,
        dry_run=dry_run,
        triggered_by=user,
        started_at=timezone.now(),
    )
    try:
        bundle = build_event_bundle(event, Audience.PUBLIC)
        validate_bundle(bundle)
        base = connector.base_url(ctx, prefix)
        urls = feed_urls(event, formats, bundle)
        export_ctx = ExportContext(
            base_url=base, feeds=urls, calendar_host=settings.EVENT_PLANNER.get("CALENDAR_HOST", "event-planner")
        )
        written, failures = [], []
        for slug in formats:
            # One format failing (e.g. PDF without system fonts) must not stop
            # the JSON/ICS feeds attendees depend on.
            try:
                files = get_exporter(slug).render(bundle, export_ctx)
            except Exception as exc:  # recorded on the run; other formats continue
                logger.exception("Exporter %s failed for %s", slug, event)
                failures.append(f"{slug}: {type(exc).__name__}: {exc}")
                continue
            for f in files:
                key = prefix + f.path
                if not dry_run:
                    connector.put(ctx, key, f.content, f.content_type)
                written.append(
                    {
                        "format": slug,
                        "path": key,
                        "url": base + f.path,
                        "bytes": len(f.content),
                        "sha256": hashlib.sha256(f.content).hexdigest(),
                    }
                )
        run.files = written
        run.bundle_version = bundle["schema_version"]
        run.session_count = len(bundle["sessions"])
        run.status = PublishRun.Status.PARTIAL if failures else PublishRun.Status.SUCCEEDED
        run.message = "\n".join(failures)
    except Exception as exc:  # record any failure on the run, then surface it
        logger.exception("Publishing %s failed", event)
        run.status = PublishRun.Status.FAILED
        run.message = f"{type(exc).__name__}: {exc}"
    run.finished_at = timezone.now()
    run.save()
    return run
