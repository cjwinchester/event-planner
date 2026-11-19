"""Create the database cache table (settings.CACHES default backend), so every
environment that migrates has it without a separate `createcachetable` step.
A no-op when CACHES uses Redis or the in-memory cache (tests)."""

from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    call_command("createcachetable", database=schema_editor.connection.alias, verbosity=0)


class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.RunPython(create_cache_table, migrations.RunPython.noop)]
