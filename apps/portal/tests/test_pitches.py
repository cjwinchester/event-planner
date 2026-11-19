from datetime import timedelta

import pytest
from allauth.account.models import EmailAddress
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event
from apps.pitches import services
from apps.pitches.models import FormQuestion, Pitch, PitchTarget
from apps.speakers import sharing
from apps.speakers.merging import claim_profiles
from apps.speakers.models import SpeakerProfile

Status = PitchTarget.Status


def login(client, django_user_model, email):
    user = django_user_model.objects.create_user(email, None)
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    claim_profiles(user)
    client.force_login(user)
    return user


@pytest.fixture
def open_call(world):
    now = timezone.now()
    world.event.pitch_window_opens_at = now - timedelta(days=1)
    world.event.pitch_window_closes_at = now + timedelta(days=10)
    world.event.save()
    FormQuestion.objects.create(event=world.event, slug="why", prompt="Why now?", required=True)
    return world


def pitch_data(world, **kw):
    data = {
        "events": [world.event.pk], "title": "Scraping court records", "description": "How we did it.",
        "format": "Panel", f"e{world.event.pk}_why": "Elections",
        "co-TOTAL_FORMS": "3", "co-INITIAL_FORMS": "0",
        "co-0-first_name": "Casey", "co-0-last_name": "Co", "co-0-email": "casey@example.com",
    }  # fmt: skip
    data.update(kw)
    return data


@pytest.mark.django_db
class TestPitching:
    def test_calls_are_public_and_list_open_events(self, client, open_call):
        world = open_call
        Event.objects.create(
            organization=world.org, name="Closed thing", slug="closed", start_date=world.event.start_date,
            end_date=world.event.end_date, timezone="UTC",
        )  # fmt: skip
        body = client.get(reverse("portal:calls")).content.decode()
        assert world.event.name in body and "Closed thing" not in body and "Log in" in body

    def test_submit_with_questions_and_co_speaker(self, client, open_call, django_user_model):
        world = open_call
        login(client, django_user_model, "alice@example.com")  # claims world.alice
        assert "Why now?" in client.get(reverse("portal:pitch-new"), {"events": world.event.pk}).content.decode()
        resp = client.post(reverse("portal:pitch-new"), pitch_data(world, **{f"e{world.event.pk}_why": ""}))
        assert resp.status_code == 422  # required question
        resp = client.post(reverse("portal:pitch-new"), pitch_data(world))
        pitch = Pitch.objects.get()
        assert resp.status_code == 302 and pitch.submitted_by == world.alice
        assert pitch.targets.get().answers == {"why": "Elections"}
        casey = SpeakerProfile.objects.get(email="casey@example.com")
        assert pitch.pitch_speakers.filter(speaker=casey).exists()
        assert sharing.can_view_private(world.org, world.alice)  # pitching shares the pitcher...
        assert not sharing.can_view_private(world.org, casey)  # ...never the co-speaker

    def test_status_hidden_until_finalized_and_combined_label(self, client, open_call, django_user_model):
        world = open_call
        login(client, django_user_model, "alice@example.com")
        client.post(reverse("portal:pitch-new"), pitch_data(world))
        target = PitchTarget.objects.get()
        services.queue_decision([target], Status.ACCEPT_QUEUE, world.admin_user)
        url = reverse("portal:pitch", args=[target.pitch_id])
        assert "Under review" in client.get(url).content.decode()
        other = services.submit_pitch(
            submitter=world.bob, title="Other", description="D", events=[world.event],
            answers_by_event={world.event.pk: {"why": "Because"}},
        )  # fmt: skip
        primary = other.targets.get()
        services.combo(primary, [target], world.admin_user)
        assert "Under review" in client.get(url).content.decode()
        primary.informed_at = timezone.now()
        primary.save()
        assert "Combined with a similar pitch" in client.get(url).content.decode()

    def test_edit_until_window_closes_then_locked(self, client, open_call, django_user_model):
        world = open_call
        login(client, django_user_model, "alice@example.com")
        client.post(reverse("portal:pitch-new"), pitch_data(world))
        pitch = Pitch.objects.get()
        edit = reverse("portal:pitch-edit", args=[pitch.pk])
        client.post(
            edit,
            pitch_data(
                world,
                title="Scraping court records, revised",
                **{"co-0-email": "", "co-0-first_name": "", "co-0-last_name": ""},
            ),
        )
        pitch.refresh_from_db()
        assert pitch.title == "Scraping court records, revised" and pitch.pitch_speakers.count() == 1  # co removed
        world.event.pitch_window_closes_at = timezone.now() - timedelta(minutes=1)
        world.event.save()
        client.post(edit, pitch_data(world, title="Too late"))
        pitch.refresh_from_db()
        assert pitch.title == "Scraping court records, revised"

    def test_withdraw_and_confirm(self, client, open_call, django_user_model):
        world = open_call
        login(client, django_user_model, "alice@example.com")
        client.post(reverse("portal:pitch-new"), pitch_data(world))
        target = PitchTarget.objects.get()
        url = reverse("portal:pitch", args=[target.pitch_id])
        client.post(url, {"target": target.pk, "action": "withdraw"})
        target.refresh_from_db()
        assert target.status == Status.WITHDRAWN
        target.status = Status.ACCEPT_QUEUE
        target.save()
        services.inform([target])
        target.refresh_from_db()
        assert target.status == Status.ACCEPTED and target.session
        client.post(url, {"target": target.pk, "action": "confirm"})
        target.refresh_from_db()
        assert target.confirmed_at and target.session.session_speakers.get(speaker=world.alice).is_confirmed
        client.post(url, {"target": target.pk, "action": "withdraw"})  # decided: use the session instead
        target.refresh_from_db()
        assert target.status == Status.ACCEPTED

    def test_co_speaker_read_only_after_signup(self, client, open_call, django_user_model):
        world = open_call
        login(client, django_user_model, "alice@example.com")
        client.post(reverse("portal:pitch-new"), pitch_data(world))
        pitch = Pitch.objects.get()
        client.logout()
        assert client.get(reverse("portal:pitch", args=[pitch.pk])).status_code == 302  # needs an account
        login(client, django_user_model, "casey@example.com")  # claims the profile Alice created
        body = client.get(reverse("portal:pitch", args=[pitch.pk])).content.decode()
        assert "added you as a co-speaker" in body and "Edit pitch" not in body
        target = pitch.targets.get()
        client.post(reverse("portal:pitch", args=[pitch.pk]), {"target": target.pk, "action": "withdraw"})
        target.refresh_from_db()
        assert target.status == Status.SUBMITTED
        assert client.get(reverse("portal:pitch-edit", args=[pitch.pk])).status_code == 404

    def test_strangers_cannot_see_pitch(self, client, open_call, django_user_model):
        world = open_call
        pitch = services.submit_pitch(
            submitter=world.bob, title="Secret", description="D", events=[world.event],
            answers_by_event={world.event.pk: {"why": "Because"}},
        )  # fmt: skip
        login(client, django_user_model, "stranger@example.com")
        client.post(reverse("portal:profile"), {
            "first_name": "S", "last_name": "T", "links-TOTAL_FORMS": "0", "links-INITIAL_FORMS": "0",
        })  # fmt: skip
        assert client.get(reverse("portal:pitch", args=[pitch.pk])).status_code == 404
