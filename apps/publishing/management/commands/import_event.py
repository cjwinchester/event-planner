"""Import an Event Bundle JSON file (the only supported import format).

    manage.py import_event bundle.json --org ire [--dry-run]

Transform legacy data (spreadsheets, old feeds, other vendors) into
schemas/event-bundle/v1.json upstream first.
"""

import json

import jsonschema
from django.core.management.base import BaseCommand, CommandError

from apps.organizations.models import Organization
from apps.publishing.importer import import_bundle


class Command(BaseCommand):
    help = "Import an Event Bundle (schemas/event-bundle/v1.json) into an organization."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--org", required=True, help="Organization slug")
        parser.add_argument("--dry-run", action="store_true", help="Validate and report, then roll back.")

    def handle(self, *args, **opts):
        try:
            org = Organization.objects.get(slug=opts["org"])
        except Organization.DoesNotExist as exc:
            raise CommandError(f"No organization {opts['org']!r}") from exc
        with open(opts["path"]) as fh:
            bundle = json.load(fh)
        try:
            result = import_bundle(bundle, org, dry_run=opts["dry_run"])
        except jsonschema.ValidationError as exc:
            where = "/".join(str(p) for p in exc.absolute_path) or "(root)"
            raise CommandError(f"Not a valid Event Bundle at {where}: {exc.message}") from exc
        for warning in result.warnings:
            self.stdout.write(self.style.WARNING(f"  ! {warning}"))
        prefix = "[dry run] " if opts["dry_run"] else ""
        self.stdout.write(self.style.SUCCESS(f"{prefix}{result.event}: {result.summary()}"))
