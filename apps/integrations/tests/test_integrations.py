from datetime import UTC, datetime

import pytest
from django.contrib.contenttypes.models import ContentType

from apps.integrations import services
from apps.integrations.base import BaseConnector, Capability, RegistrationRecord
from apps.integrations.models import ExternalIdentity, IntegrationConfig, SyncRun
from apps.integrations.registry import all_connectors, register


def test_registry_has_builtins_and_enforces_capabilities():
    assert {"membersuite", "memberclicks", "zoom", "vimeo", "s3", "local"} <= set(all_connectors())

    class Liar(BaseConnector):
        slug = "liar"
        capabilities = frozenset({Capability.REGISTRATION_SYNC})  # but no fetch_registrations

    with pytest.raises(TypeError):
        register(Liar)


class FakeAMS(BaseConnector):
    slug = "fake-ams"
    capabilities = frozenset({Capability.REGISTRATION_SYNC})

    def fetch_registrations(self, ctx, *, external_event_id):
        assert ctx.credentials == {"token": "t"} and external_event_id == "EXT-1"
        yield RegistrationRecord(
            external_id="r1", registered_at=datetime(2027, 1, 5, tzinfo=UTC), first_name="Ann", city="Denver"
        )


@pytest.mark.django_db
def test_sync_registrations_upserts(world, monkeypatch):
    monkeypatch.setattr(services, "get_connector", lambda slug: FakeAMS())
    IntegrationConfig.objects.create(organization=world.org, connector="fake-ams", credentials={"token": "t"})
    ExternalIdentity.objects.create(
        organization=world.org,
        connector="fake-ams",
        external_id="EXT-1",
        content_type=ContentType.objects.get_for_model(world.event),
        object_id=world.event.pk,
    )
    world.event.registration_connector = "fake-ams"
    world.event.save()
    run = services.sync_registrations(world.event)
    assert run.status == SyncRun.Status.SUCCEEDED and run.stats == {"created": 1, "updated": 0}
    run = services.sync_registrations(world.event)
    assert run.stats == {"created": 0, "updated": 1}
    assert world.event.registrations.get().city == "Denver"


@pytest.mark.django_db
def test_sync_fails_cleanly_when_not_configured(world):
    world.event.registration_connector = "membersuite"
    world.event.save()
    run = services.sync_registrations(world.event)
    assert run.status == SyncRun.Status.FAILED and "Link this event" in run.message
