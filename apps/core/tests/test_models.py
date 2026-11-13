import pytest
from django.db import connection

from apps.core.models import resolve_organization
from apps.integrations.models import IntegrationConfig


@pytest.mark.django_db
def test_encrypted_credentials_round_trip_and_ciphertext_at_rest(world):
    cfg = IntegrationConfig.objects.create(
        organization=world.org, connector="membersuite", credentials={"password": "hunter2"}
    )
    cfg.refresh_from_db()
    assert cfg.credentials == {"password": "hunter2"}
    with connection.cursor() as cur:
        cur.execute("select credentials from integrations_integrationconfig where id = %s", [cfg.pk])
        raw = cur.fetchone()[0]
    assert "hunter2" not in raw


@pytest.mark.django_db
def test_get_credentials_merges_org_then_event_scope(world):
    IntegrationConfig.objects.create(
        organization=world.org, connector="zoom", credentials={"client_id": "org", "secret": "s"}
    )
    IntegrationConfig.objects.create(
        organization=world.org, event=world.event, connector="zoom", credentials={"client_id": "event"}
    )
    creds, _ = IntegrationConfig.get_credentials(world.org, "zoom", event=world.event)
    assert creds == {"client_id": "event", "secret": "s"}
    assert IntegrationConfig.get_credentials(world.other_org, "zoom") is None


@pytest.mark.django_db
def test_org_scoped_queryset_for_user_and_visibility(world):
    from apps.core.models import Visibility
    from apps.events.models import Event

    other_event = Event.objects.create(
        organization=world.other_org,
        name="Other conf",
        slug="other-conf",
        start_date=world.event.start_date,
        end_date=world.event.end_date,
        timezone="UTC",
        visibility=Visibility.PRIVATE,
    )
    assert list(Event.objects.for_user(world.staff_user)) == [world.event]
    assert list(Event.objects.for_user(world.outsider)) == [other_event]
    # NETWORK-visible events are visible to other orgs; PRIVATE ones aren't.
    assert world.event in Event.objects.visible_to_organization(world.other_org)
    assert other_event not in Event.objects.visible_to_organization(world.org)


@pytest.mark.django_db
def test_resolve_organization_walks_org_path(world, make_session):
    session = make_session()
    assert resolve_organization(session) == world.org
    assert resolve_organization(world.org) == world.org


@pytest.mark.django_db
def test_membership_grants_admin_access(world):
    assert world.staff_user.is_staff
    world.staff_user.memberships.all().delete()
    world.staff_user.refresh_from_db()
    assert not world.staff_user.is_staff
