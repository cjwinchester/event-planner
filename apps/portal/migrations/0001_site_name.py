"""Replace Django's placeholder site ("example.com"), which allauth puts in
every email ("Hello from example.com!"), with SITE_NAME / SITE_URL."""

from urllib.parse import urlparse

from django.conf import settings
from django.db import migrations


def set_site(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    site, _ = Site.objects.get_or_create(pk=settings.SITE_ID, defaults={"domain": "example.com", "name": "example.com"})
    if site.domain != "example.com":
        return  # already configured for this deployment
    url = settings.EVENT_PLANNER.get("SITE_URL") or "http://localhost:8000"
    site.domain = urlparse(url).netloc or "localhost:8000"
    site.name = settings.SITE_NAME
    site.save()


class Migration(migrations.Migration):
    dependencies = [("sites", "0002_alter_domain_unique")]
    operations = [migrations.RunPython(set_site, migrations.RunPython.noop)]
