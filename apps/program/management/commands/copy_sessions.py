"""Copy stock (or all) sessions from one event to another.

    manage.py copy_sessions ire/nicar-2026 ire/nicar-2027
    manage.py copy_sessions ire/nicar-2026 ire/nicar-2027 --all --speakers --keep-schedule --dry-run

Events are given as <org-slug>/<event-slug>.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.events.models import Event
from apps.program.copying import copy_sessions


def _event(ref: str) -> Event:
    try:
        org_slug, event_slug = ref.split("/", 1)
        return Event.objects.select_related("organization").get(organization__slug=org_slug, slug=event_slug)
    except (ValueError, Event.DoesNotExist) as exc:
        raise CommandError(f"No event {ref!r} (use <org-slug>/<event-slug>)") from exc


class Command(BaseCommand):
    help = "Copy stock sessions from one event to another (same organization)."

    def add_arguments(self, parser):
        parser.add_argument("source")
        parser.add_argument("target")
        parser.add_argument("--all", action="store_true", help="Copy every active session, not just stock ones.")
        parser.add_argument("--speakers", action="store_true", help="Also copy speaker assignments.")
        parser.add_argument("--keep-schedule", action="store_true", help="Keep day/time offset and matching rooms.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        source, target = _event(opts["source"]), _event(opts["target"])
        sessions = source.sessions.active() if opts["all"] else source.sessions.stock()
        with transaction.atomic():
            result = copy_sessions(
                source, target, sessions, include_speakers=opts["speakers"], keep_schedule=opts["keep_schedule"]
            )
            for s in result.created:
                self.stdout.write(f"  + {s.public_id} {s.title}")
            for s, why in result.skipped:
                self.stdout.write(f"  - {s.title}: {why}")
            self.stdout.write(self.style.SUCCESS(f"{len(result.created)} copied, {len(result.skipped)} skipped."))
            if opts["dry_run"]:
                transaction.set_rollback(True)
                self.stdout.write("Dry run — rolled back.")
