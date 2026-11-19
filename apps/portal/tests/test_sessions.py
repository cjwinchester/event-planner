from datetime import timedelta

import pytest
from allauth.account.models import EmailAddress
from django.urls import reverse
from django.utils import timezone

from apps.pitches.models import FormQuestion
from apps.program.models import SessionSpeaker, SpeakerUpdate
from apps.publishing.bundle import Audience, build_event_bundle
from apps.speakers.models import EventSpeaker
from conftest import at


@pytest.fixture
def alice_portal(client, world, make_session, django_user_model):
    """Alice (a speaker) logged in, on one unconfirmed session with Bob."""
    user = django_user_model.objects.create_user("alice@example.com", None)
    EmailAddress.objects.create(user=user, email="alice@example.com", verified=True, primary=True)
    world.alice.user = user
    world.alice.save()
    session = make_session(
        "FOIA tactics", room=world.er_a, start=at(5, 10), end=at(5, 11), speakers=[world.alice, world.bob],
        confirmed=False,
    )  # fmt: skip
    world.event.publish_schedule = True
    world.event.save()
    client.force_login(user)
    return world, session, SessionSpeaker.objects.get(session=session, speaker=world.alice)


@pytest.mark.django_db
class TestMySessions:
    def test_list_and_confirm_publishes_speaker(self, client, alice_portal):
        world, session, ss = alice_portal
        body = client.get(reverse("portal:sessions")).content.decode()
        assert "FOIA tactics" in body and "Please confirm" in body and "Bob Builder" in body
        assert "10:00 AM–11:00 AM EST" in body  # event-local time, not UTC
        public = build_event_bundle(world.event, Audience.PUBLIC)
        assert next(s for s in public["sessions"] if s["id"] == session.public_id)["speakers"] == []
        client.post(reverse("portal:session", args=[ss.pk]), {"action": "confirm"})
        ss.refresh_from_db()
        assert ss.is_confirmed and ss.confirmed_at
        assert EventSpeaker.objects.get(event=world.event, speaker=world.alice).status == "confirmed"
        assert SpeakerUpdate.objects.get().kind == "confirmed"
        public = build_event_bundle(world.event, Audience.PUBLIC)
        assert [
            x["speaker_id"] for x in next(s for s in public["sessions"] if s["id"] == session.public_id)["speakers"]
        ] == [world.alice.public_id]

    def test_edit_only_before_deadline_and_logged(self, client, alice_portal):
        world, session, ss = alice_portal
        url = reverse("portal:session", args=[ss.pk])
        assert "aren't open" in client.get(url).content.decode()  # no deadline set: staff only
        client.post(url, {"action": "edit", "title": "Hijack"})
        session.refresh_from_db()
        assert session.title == "FOIA tactics"
        world.event.speaker_edit_deadline = timezone.now() + timedelta(days=1)
        world.event.save()
        client.post(url, {"action": "edit", "title": "FOIA tactics that work", "summary": "", "description": "New"})
        session.refresh_from_db()
        assert session.title == "FOIA tactics that work"
        update = SpeakerUpdate.objects.get(kind="edited")
        assert update.changes["title"] == ["FOIA tactics", "FOIA tactics that work"] and update.speaker == world.alice
        world.event.speaker_edit_deadline = timezone.now() - timedelta(minutes=1)
        world.event.save()
        client.post(url, {"action": "edit", "title": "Too late"})
        session.refresh_from_db()
        assert session.title == "FOIA tactics that work"

    def test_withdraw_always_allowed(self, client, alice_portal):
        world, session, ss = alice_portal
        EventSpeaker.objects.create(event=world.event, speaker=world.alice, status="confirmed")
        client.post(reverse("portal:session-withdraw", args=[ss.pk]), {"reason": "Family emergency"})
        assert not SessionSpeaker.objects.filter(pk=ss.pk).exists()
        assert SessionSpeaker.objects.filter(session=session, speaker=world.bob).exists()
        update = SpeakerUpdate.objects.get(kind="withdrew")
        assert update.changes == {"reason": "Family emergency"} and update.session_title == "FOIA tactics"
        assert EventSpeaker.objects.get(event=world.event, speaker=world.alice).status == "withdrawn"

    def test_cannot_touch_someone_elses_role(self, client, alice_portal):
        world, session, _ = alice_portal
        bobs = SessionSpeaker.objects.get(session=session, speaker=world.bob)
        assert client.get(reverse("portal:session", args=[bobs.pk])).status_code == 404
        assert client.post(reverse("portal:session-withdraw", args=[bobs.pk])).status_code == 404

    def test_times_hidden_until_schedule_published(self, client, alice_portal):
        world, _, ss = alice_portal
        world.event.publish_schedule = False
        world.event.save()
        assert "to be announced" in client.get(reverse("portal:session", args=[ss.pk])).content.decode()

    def test_event_info_form(self, client, alice_portal):
        world, *_ = alice_portal
        FormQuestion.objects.create(event=world.event, form="speaker_info", slug="diet", prompt="Dietary needs?")
        FormQuestion.objects.create(
            event=world.event, form="speaker_info", slug="shirt", prompt="Shirt size", kind="single_choice",
            choices=["S", "M", "L"], required=True,
        )  # fmt: skip
        url = reverse("portal:event-info", args=[world.event.pk])
        assert "Dietary needs?" in client.get(url).content.decode()
        assert client.post(url, {"q_diet": "None"}).status_code == 200  # shirt is required
        client.post(url, {"q_diet": "Vegetarian", "q_shirt": "M", "recording_consent": "yes"})
        es = EventSpeaker.objects.get(event=world.event, speaker=world.alice)
        assert es.form_responses == {"diet": "Vegetarian", "shirt": "M"} and es.recording_consent is True


@pytest.mark.django_db
class TestStaffSide:
    def test_updates_page_review_and_deadline(self, client, alice_portal):
        world, _session, ss = alice_portal
        client.post(reverse("portal:session-withdraw", args=[ss.pk]), {"reason": "Sick"})
        client.force_login(world.staff_user)
        url = reverse("planner:speaker-updates", args=[world.org.slug, world.event.slug])
        body = client.get(url).content.decode()
        assert "Withdrew" in body and "Sick" in body and "Bob Builder" in body  # Bob still unconfirmed
        client.post(reverse("planner:speaker-updates-review", args=[world.org.slug, world.event.slug]))
        assert not SpeakerUpdate.objects.filter(reviewed_at__isnull=True).exists()
        client.post(reverse("planner:speaker-deadline", args=[world.org.slug, world.event.slug]),
                    {"deadline": "2027-02-20T17:00"})  # fmt: skip
        world.event.refresh_from_db()
        assert world.event.speaker_edit_deadline.astimezone(world.event.tz).hour == 17
        client.force_login(world.viewer_user)
        assert client.get(url).status_code == 403

    def test_staff_mark_confirmed_from_panel(self, client, alice_portal):
        world, session, ss = alice_portal
        client.force_login(world.staff_user)
        client.post(reverse("planner:session-speaker-confirm", args=[session.pk, ss.pk]))
        ss.refresh_from_db()
        assert ss.is_confirmed and not SpeakerUpdate.objects.exists()  # staff actions aren't "speaker updates"
