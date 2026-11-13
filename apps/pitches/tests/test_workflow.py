from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.pitches import services
from apps.pitches.models import FormQuestion, PitchTarget
from apps.speakers import sharing

Status = PitchTarget.Status


@pytest.fixture
def open_event(world):
    now = timezone.now()
    world.event.pitch_window_opens_at = now - timedelta(days=1)
    world.event.pitch_window_closes_at = now + timedelta(days=1)
    world.event.save()
    return world.event


@pytest.mark.django_db
def test_submit_requires_open_window(world):
    with pytest.raises(ValidationError):
        services.submit_pitch(submitter=world.alice, title="T", description="D", events=[world.event])


@pytest.mark.django_db
def test_submit_grants_sharing_and_validates_answers(world, open_event):
    FormQuestion.objects.create(
        event=open_event, slug="lab", prompt="Lab?", kind="single_choice", choices=["PC", "Mac"], required=True
    )
    with pytest.raises(ValidationError):
        services.submit_pitch(submitter=world.alice, title="T", description="D", events=[open_event])
    pitch = services.submit_pitch(
        submitter=world.alice,
        title="Scraping 101",
        description="D",
        events=[open_event],
        co_speakers=[world.bob],
        answers_by_event={open_event.pk: {"lab": "Mac"}},
        format="Hands-on",
    )
    target = pitch.targets.get()
    assert target.answers == {"lab": "Mac"}
    assert sharing.can_view_private(world.org, world.alice)
    assert not sharing.can_view_private(world.other_org, world.alice)


@pytest.mark.django_db
def test_queue_then_inform_creates_sessions_once(world, open_event):
    pitch = services.submit_pitch(
        submitter=world.alice,
        title="Scraping 101",
        description="D",
        events=[open_event],
        co_speakers=[world.bob],
        format="Hands-on",
    )
    target = pitch.targets.get()
    services.queue_decision([target], Status.ACCEPT_QUEUE, world.staff_user)
    target.refresh_from_db()
    assert target.status == Status.ACCEPT_QUEUE and target.session is None and target.informed_at is None

    counts = services.inform(PitchTarget.objects.filter(pk=target.pk))
    target.refresh_from_db()
    assert counts["accepted"] == 1 and target.status == Status.ACCEPTED and target.informed_at
    session = target.session
    assert session.source == "pitch" and session.session_type == world.hands_on
    assert set(session.session_speakers.values_list("speaker__first_name", flat=True)) == {"Alice", "Bob"}
    assert open_event.event_speakers.count() == 2
    assert services.accept(target) == session  # idempotent


@pytest.mark.django_db
def test_merge_folds_speakers_into_primary(world, open_event):
    from apps.speakers.models import SpeakerProfile

    carol = SpeakerProfile.objects.create(first_name="Carol")
    p1 = services.submit_pitch(submitter=world.alice, title="A", description="D", events=[open_event])
    p2 = services.submit_pitch(submitter=carol, title="B", description="D", events=[open_event])
    t1, t2 = p1.targets.get(), p2.targets.get()
    services.merge(t1, [t2], world.staff_user)
    t2.refresh_from_db()
    assert t2.status == Status.MERGED and t2.merged_into == t1
    session = services.accept(t1)
    assert set(session.session_speakers.values_list("speaker__first_name", flat=True)) == {"Alice", "Carol"}
    with pytest.raises(ValidationError):
        services.accept(t2)
