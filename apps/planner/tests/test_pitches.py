import pytest
from django.urls import reverse

from apps.organizations.models import Membership
from apps.pitches import services
from apps.pitches.models import Pitch, PitchTarget, PitchVote
from apps.speakers import sharing
from apps.speakers.models import SpeakerShareGrant

Status = PitchTarget.Status


@pytest.fixture
def review(world, django_user_model):
    staff2 = django_user_model.objects.create_user("second@ire.org", "pw", first_name="Second", last_name="Planner")
    Membership.objects.create(user=staff2, organization=world.org, role=Membership.Role.STAFF)

    def pitch(title, speaker, **kw):
        p = services.submit_pitch(
            submitter=speaker, title=title, description="Desc", events=[world.event], on_behalf=True, **kw
        )
        return p.targets.get()

    t1 = pitch("Scraping 101", world.alice, format="Hands-on")
    t2 = pitch("FOIA tactics", world.bob, format="Panel")
    t3 = pitch("FOIA deep dive", world.bob, format="Panel")
    services.cast_vote(t1, staff2, "yes", "Love it")
    return world, staff2, t1, t2, t3


def url(world, name="pitches"):
    return reverse(f"planner:{name}", args=[world.org.slug, world.event.slug])


@pytest.mark.django_db
class TestDashboard:
    def test_blind_until_you_vote(self, client, review):
        world, _staff2, t1, *_ = review
        client.force_login(world.staff_user)
        body = client.get(url(world)).content.decode()
        assert "Scraping 101" in body and "vote to see" in body and "Love it" not in body
        panel = client.get(reverse("planner:pitch-panel", args=[t1.pk])).content.decode()
        assert "hidden until you vote" in panel and "Love it" not in panel

        row = client.post(reverse("planner:pitch-vote", args=[t1.pk]), {"vote": "no"}).content.decode()
        assert '<b class="yes">1</b>/<b class="maybe">0</b>/<b class="no">1</b>' in row
        assert "Love it" in client.get(reverse("planner:pitch-panel", args=[t1.pk])).content.decode()

    def test_queued_decision_reveals_votes(self, client, review):
        world, staff2, t1, *_ = review
        services.queue_decision([t1], Status.ACCEPT_QUEUE, staff2)
        client.force_login(world.staff_user)
        panel = client.get(reverse("planner:pitch-panel", args=[t1.pk])).content.decode()
        assert "Love it" in panel

    def test_unvoted_filter_and_counts(self, client, review):
        world, staff2, _t1, _t2, _t3 = review
        client.force_login(staff2)
        body = client.get(url(world), {"unvoted": "1"}).content.decode()
        assert "Scraping 101" not in body and "FOIA tactics" in body
        assert "You haven't voted on 2" in body

    def test_support_sort_puts_blind_rows_last(self, client, review):
        world, _staff2, _t1, t2, t3 = review
        client.force_login(world.staff_user)
        services.cast_vote(t2, world.staff_user, "yes")
        services.cast_vote(t3, world.staff_user, "no")
        body = client.get(url(world), {"sort": "support", "partial": "table"}).content.decode()
        assert body.index("FOIA tactics") < body.index("FOIA deep dive") < body.index("Scraping 101")

    def test_permissions(self, client, review):
        world, _staff2, t1, *_ = review
        client.force_login(world.viewer_user)
        assert client.get(url(world)).status_code == 200
        assert client.post(reverse("planner:pitch-vote", args=[t1.pk]), {"vote": "yes"}).status_code == 403
        client.force_login(world.outsider)
        assert client.get(url(world)).status_code == 403
        assert client.get(reverse("planner:pitch-panel", args=[t1.pk])).status_code == 403


@pytest.mark.django_db
class TestBulkAndFinalize:
    def test_bulk_queue_assign_and_combo(self, client, review):
        world, staff2, t1, t2, t3 = review
        client.force_login(world.staff_user)
        bulk = url(world, "pitch-bulk")
        client.post(bulk, {"ids": [t1.pk, t2.pk], "action": "queue_accept"})
        assert set(PitchTarget.objects.filter(status=Status.ACCEPT_QUEUE)) == {t1, t2}
        client.post(bulk, {"ids": [t3.pk], "action": "assign", "assignee": staff2.pk})
        t3.refresh_from_db()
        assert t3.assigned_to == staff2
        resp = client.post(bulk, {"ids": [t2.pk, t3.pk], "action": "combo", "primary": t2.pk})
        assert "Combo&#x27;d 1 pitch" in resp.content.decode() or "Combo'd 1 pitch" in resp.content.decode()
        t3.refresh_from_db()
        assert t3.status == Status.MERGED and t3.merged_into == t2
        assert client.post(bulk, {"ids": [t1.pk], "action": "combo"}).status_code == 422

    def test_only_admins_finalize(self, client, review):
        world, staff2, t1, t2, _ = review
        services.queue_decision([t1], Status.ACCEPT_QUEUE, staff2)
        services.queue_decision([t2], Status.DECLINE_QUEUE, staff2)
        client.force_login(world.staff_user)
        page = client.get(url(world, "pitch-finalize")).content.decode()
        assert "Only organization admins can finalize" in page and "Scraping 101" in page
        assert client.post(url(world, "pitch-finalize")).status_code == 403
        client.force_login(world.admin_user)
        resp = client.post(url(world, "pitch-finalize"))
        assert "1 accepted" in resp.content.decode() and "1 declined" in resp.content.decode()
        t1.refresh_from_db()
        assert t1.status == Status.ACCEPTED and t1.session is not None and t1.informed_at


@pytest.mark.django_db
class TestStaffEnteredPitch:
    def test_new_speaker_and_pitch(self, client, world):
        client.force_login(world.staff_user)
        resp = client.post(
            url(world, "pitch-new"),
            {
                "first_name": "Casey",
                "last_name": "New",
                "email": "casey@example.com",
                "affiliation": "Paper",
                "title": "Staff idea",
                "description": "From the ideas pool",
                "format": "Panel",
                "tracks": [world.track_ai.pk],
                "staff_notes": "Ask Casey",
            },
        )
        assert resp.status_code == 200 and "Staff idea" in resp.content.decode()
        target = PitchTarget.objects.get(pitch__title="Staff idea")
        assert target.pitch.source == Pitch.Source.STAFF and target.assigned_to == world.staff_user
        assert list(target.suggested_tracks.all()) == [world.track_ai]
        grant = SpeakerShareGrant.objects.get(speaker=target.pitch.submitted_by, organization=world.org)
        assert grant.source == SpeakerShareGrant.Source.CREATED

    def test_on_behalf_does_not_override_revocation(self, client, world):
        sharing.grant(world.alice, world.org, SpeakerShareGrant.Source.PITCH)
        sharing.revoke(world.alice, world.org, by_speaker=True)
        client.force_login(world.staff_user)
        client.post(url(world, "pitch-new"), {"speaker": world.alice.pk, "title": "T", "description": "D"})
        assert PitchTarget.objects.filter(pitch__submitted_by=world.alice).exists()
        assert not sharing.can_view_private(world.org, world.alice)

    def test_existing_speaker_matched_by_email(self, client, world):
        client.force_login(world.staff_user)
        client.post(url(world, "pitch-new"), {"email": "ALICE@example.com", "title": "T", "description": "D"})
        assert PitchTarget.objects.get().pitch.submitted_by == world.alice

    def test_requires_speaker_and_text(self, client, world):
        client.force_login(world.staff_user)
        assert client.post(url(world, "pitch-new"), {"title": "T"}).status_code == 422


@pytest.mark.django_db
def test_panel_saves_review_fields(client, review):
    world, staff2, t1, *_ = review
    client.force_login(world.staff_user)
    resp = client.post(
        reverse("planner:pitch-panel", args=[t1.pk]),
        {"staff_notes": "Pair with lab", "assigned_to": staff2.pk, "tracks": [world.track_records.pk]},
    )
    assert resp["HX-Trigger"] == "pitches:refresh"
    t1.refresh_from_db()
    assert t1.staff_notes == "Pair with lab" and t1.assigned_to == staff2
    assert list(t1.suggested_tracks.all()) == [world.track_records]


@pytest.mark.django_db
def test_api_votes_are_blind(review):
    from rest_framework.test import APIClient

    world, _staff2, t1, *_ = review
    c = APIClient()
    c.force_authenticate(world.staff_user)
    row = next(r for r in c.get("/api/v1/pitch-reviews/").json()["results"] if r["id"] == t1.pk)
    assert row["votes"] is None
    c.post(f"/api/v1/pitch-reviews/{t1.pk}/vote/", {"vote": "yes"}, format="json")
    row = next(r for r in c.get("/api/v1/pitch-reviews/").json()["results"] if r["id"] == t1.pk)
    assert len(row["votes"]) == 2
    assert PitchVote.objects.filter(target=t1).count() == 2
