"""Write an event's Event Bundle to stdout or a file.

manage.py export_event ire/nicar-2027 [--internal] [-o bundle.json]
"""

import json

from django.core.management.base import BaseCommand, CommandError

from apps.events.models import Event
from apps.publishing.bundle import Audience, build_event_bundle, validate_bundle


class Command(BaseCommand):
    help = "Export an Event Bundle (public by default; --internal includes unscheduled sessions)."

    def add_arguments(self, parser):
        parser.add_argument("event", help="<org-slug>/<event-slug>")
        parser.add_argument("--internal", action="store_true")
        parser.add_argument("-o", "--output")

    def handle(self, *args, **opts):
        try:
            org_slug, event_slug = opts["event"].split("/", 1)
            event = Event.objects.get(organization__slug=org_slug, slug=event_slug)
        except (ValueError, Event.DoesNotExist) as exc:
            raise CommandError("Event not found (use <org-slug>/<event-slug>)") from exc
        bundle = build_event_bundle(event, Audience.INTERNAL if opts["internal"] else Audience.PUBLIC)
        validate_bundle(bundle)
        text = json.dumps(bundle, indent=2, ensure_ascii=False)
        if opts["output"]:
            with open(opts["output"], "w") as fh:
                fh.write(text)
        else:
            self.stdout.write(text)
