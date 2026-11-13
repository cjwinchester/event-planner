"""Publish an event's feeds now (synchronously).

manage.py publish_event ire/nicar-2027 [--formats json,ics] [--dry-run]
"""

from django.core.management.base import BaseCommand, CommandError

from apps.events.models import Event
from apps.publishing.services import publish_event


class Command(BaseCommand):
    help = "Render and upload an event's published feeds."

    def add_arguments(self, parser):
        parser.add_argument("event", help="<org-slug>/<event-slug>")
        parser.add_argument("--formats", help="Comma-separated exporter slugs (default: all default exporters)")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        try:
            org_slug, event_slug = opts["event"].split("/", 1)
            event = Event.objects.get(organization__slug=org_slug, slug=event_slug)
        except (ValueError, Event.DoesNotExist) as exc:
            raise CommandError("Event not found (use <org-slug>/<event-slug>)") from exc
        formats = opts["formats"].split(",") if opts["formats"] else None
        run = publish_event(event, formats=formats, dry_run=opts["dry_run"])
        if run.status != run.Status.SUCCEEDED:
            raise CommandError(run.message)
        for f in run.files:
            self.stdout.write(f"  {f['url']}  ({f['bytes']} bytes)")
        self.stdout.write(self.style.SUCCESS(f"{run.session_count} sessions published to {run.target}."))
