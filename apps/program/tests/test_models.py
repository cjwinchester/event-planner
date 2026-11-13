import pytest
from django.core.exceptions import ValidationError

from apps.program.models import Session
from conftest import at


@pytest.mark.django_db
def test_public_ids_start_at_org_setting_and_increment(world, make_session):
    first = make_session("One")
    second = make_session("Two")
    assert first.public_id == world.org.session_id_start
    assert second.public_id == first.public_id + 1


@pytest.mark.django_db
def test_ready_requires_room_time_and_edited_title(world, make_session):
    draft = make_session("Draft", edited=False, room=world.er_a, start=at(5, 9), end=at(5, 10))
    unplaced = make_session("Unplaced")
    ready = make_session("Ready", room=world.er_b, start=at(5, 9), end=at(5, 10))
    assert not draft.is_ready and "title not edited" in draft.readiness_problems
    assert not unplaced.is_ready
    assert ready.is_ready
    assert list(Session.objects.ready()) == [ready]


@pytest.mark.django_db
def test_times_must_fall_within_event(world, make_session):
    session = make_session("Late", room=world.er_a)
    session.starts_at, session.ends_at = at(9, 9), at(9, 10)  # event ends 3/7
    with pytest.raises(ValidationError):
        session.full_clean()


@pytest.mark.django_db
def test_room_atoms_for_combined_rooms(world):
    assert world.room_ab.atoms() == {world.room_a.pk, world.room_b.pk}
    assert world.room_c.atoms() == {world.room_c.pk}


@pytest.mark.django_db
def test_recording_inherits_room_default(world, make_session):
    in_b = make_session(room=world.er_b, start=at(5, 9), end=at(5, 10))
    assert in_b.effective_is_recorded
    in_b.is_recorded = False
    assert not in_b.effective_is_recorded
