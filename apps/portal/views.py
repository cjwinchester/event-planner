"""Speaker portal (/speak/): a speaker's own profile, who may see their
contact details, and (parts B/C) their sessions and pitches.

Any logged-in user may use it; it only ever shows the user's *own* speaker
profile (`SpeakerProfile.user`). Profiles are linked on login by verified
email (`speakers.merging.claim_profiles`), or created here from scratch.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.attendees.models import AttendeePass
from apps.organizations.models import Organization
from apps.organizations.permissions import organization_ids_for
from apps.program import speaker_actions
from apps.program.models import SessionSpeaker
from apps.speakers import sharing
from apps.speakers.merging import claim_profiles
from apps.speakers.models import EventSpeaker, SpeakerProfile, SpeakerShareGrant

from .forms import EventInfoForm, GrantForm, LinkFormSet, ProfileForm, SessionEditForm, WithdrawForm


def home(request):
    """'/': staff go to the planner, everyone else to the speaker portal."""
    if not request.user.is_authenticated:
        return redirect("account_login")
    if organization_ids_for(request.user):
        return redirect("planner:event-list")
    return redirect("portal:home")


def _profile(request) -> SpeakerProfile | None:
    profile = SpeakerProfile.objects.filter(user=request.user, merged_into__isnull=True).first()
    return profile or claim_profiles(request.user)


def _ctx(request, **extra):
    return {"is_staff_member": bool(organization_ids_for(request.user)), **extra}


@login_required
def portal_home(request):
    profile = _profile(request)
    if profile is None:
        return redirect("portal:profile")
    grants = profile.share_grants.select_related("organization")
    missing = [label for f, label in (("bio", "a bio"), ("affiliation", "your affiliation")) if not getattr(profile, f)]
    if not profile.headshot_href:
        missing.append("a headshot")
    return render(
        request,
        "portal/home.html",
        _ctx(
            request,
            profile=profile,
            public=sharing.visible_profile(profile),
            active_grants=[g for g in grants if g.is_active],
            missing=missing,
        ),
    )


@login_required
def profile(request):
    instance = _profile(request)
    creating = instance is None
    if creating:
        instance = SpeakerProfile(user=request.user, email=request.user.email)
    form = ProfileForm(request.POST or None, request.FILES or None, instance=instance)
    links = LinkFormSet(request.POST or None, instance=instance, prefix="links")
    if request.method == "POST" and form.is_valid() and links.is_valid():
        with transaction.atomic():
            saved = form.save()
            links.instance = saved
            links.save()
        messages.success(request, "Profile created." if creating else "Profile saved.")
        return redirect("portal:home")
    return render(
        request,
        "portal/profile.html",
        _ctx(request, form=form, links=links, creating=creating, profile=None if creating else instance),
        status=422 if request.method == "POST" else 200,
    )


@login_required
def sharing_view(request):
    profile = _profile(request)
    if profile is None:
        return redirect("portal:profile")
    grants = list(profile.share_grants.select_related("organization").order_by("organization__name"))
    active_ids = [g.organization_id for g in grants if g.is_active]
    form = GrantForm(request.POST or None, exclude_ids=active_ids)
    if request.method == "POST" and form.is_valid():
        org = form.cleaned_data["organization"]
        sharing.grant(profile, org, SpeakerShareGrant.Source.SPEAKER)
        messages.success(request, f"{org.name} can now see your contact details.")
        return redirect("portal:sharing")
    return render(
        request,
        "portal/sharing.html",
        _ctx(request, profile=profile, grants=grants, form=form, private_fields=SpeakerProfile.PRIVATE_FIELDS),
    )


@login_required
@require_POST
def sharing_revoke(request, org_id):
    profile = _profile(request)
    if profile is None:
        return redirect("portal:profile")
    org = get_object_or_404(Organization, pk=org_id)
    sharing.revoke(profile, org, by_speaker=True)
    messages.success(request, f"{org.name} can no longer see your contact details.")
    return redirect("portal:sharing")


# ------------------------------------------------------------------ part B: my sessions


def _my_role(request, pk) -> SessionSpeaker:
    """One of the logged-in speaker's own session roles (404 for anyone else's)."""
    profile = _profile(request)
    return get_object_or_404(
        SessionSpeaker.objects.select_related("session__event__organization", "session__room__room", "speaker"),
        pk=pk,
        speaker=profile,
    )


def _session_row(ss: SessionSpeaker) -> dict:
    s, event = ss.session, ss.session.event
    # Times and rooms are only shown once the organizers publish the schedule.
    show_when = event.publish_schedule and s.starts_at and s.ends_at
    return {
        "ss": ss,
        "session": s,
        "event": event,
        "starts": s.starts_at.astimezone(event.tz) if show_when else None,
        "ends": s.ends_at.astimezone(event.tz) if show_when else None,
        "room": s.room.name if show_when and s.room_id and s.room.is_public else "",
        "co_speakers": [x.speaker.full_name for x in s.session_speakers.all() if x.speaker_id != ss.speaker_id],
        "can_edit": speaker_actions.can_edit(event) and not s.is_canceled,
        "upcoming": event.end_date >= timezone.localdate(),
    }


@login_required
def sessions(request):
    profile = _profile(request)
    if profile is None:
        return redirect("portal:profile")
    roles = (
        SessionSpeaker.objects.filter(speaker=profile)
        .select_related("session__event__organization", "session__room__room")
        .prefetch_related("session__session_speakers__speaker")
        .order_by("-session__event__start_date", "session__starts_at")
    )
    rows = [_session_row(ss) for ss in roles]
    return render(
        request,
        "portal/sessions.html",
        _ctx(request, upcoming=[r for r in rows if r["upcoming"]], past=[r for r in rows if not r["upcoming"]]),
    )


@login_required
def session_detail(request, pk):
    ss = _my_role(request, pk)
    row = _session_row(ss)
    form = None
    if row["can_edit"]:
        initial = {f: getattr(ss.session, f) for f in speaker_actions.EDITABLE_FIELDS}
        form = SessionEditForm(request.POST or None, initial=initial)
        if request.method == "POST" and request.POST.get("action") == "edit" and form.is_valid():
            try:
                changed = speaker_actions.edit_session(ss, request.user, form.cleaned_data)
            except speaker_actions.SpeakerActionError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request, "Saved. The organizers can see what you changed." if changed else "No changes."
                )
            return redirect("portal:session", pk=ss.pk)
    if request.method == "POST" and request.POST.get("action") == "confirm":
        speaker_actions.confirm(ss, request.user)
        messages.success(request, "Thanks for confirming!")
        return redirect("portal:session", pk=ss.pk)
    passes = AttendeePass.objects.filter(event=ss.session.event, speaker=ss.speaker, revoked_at__isnull=True)
    return render(
        request,
        "portal/session.html",
        _ctx(request, row=row, form=form, my_pass=passes.first(), deadline=ss.session.event.speaker_edit_deadline),
    )


@login_required
def session_withdraw(request, pk):
    ss = _my_role(request, pk)
    form = WithdrawForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        title = ss.session.title
        speaker_actions.withdraw(ss, request.user, reason=form.cleaned_data["reason"])
        messages.success(request, f"You've withdrawn from “{title}”. The organizers have been notified.")
        return redirect("portal:sessions")
    return render(request, "portal/withdraw.html", _ctx(request, row=_session_row(ss), form=form))


@login_required
def event_info(request, event_id):
    """The event's speaker-info form and recording consent (stored on the
    speaker's EventSpeaker row, which only that organization can see)."""
    from apps.events.models import Event
    from apps.pitches.models import FormQuestion
    from apps.pitches.services import validate_answers

    profile = _profile(request)
    if profile is None:
        return redirect("portal:profile")
    event = get_object_or_404(Event.objects.filter(sessions__session_speakers__speaker=profile).distinct(), pk=event_id)
    es, _ = EventSpeaker.objects.get_or_create(event=event, speaker=profile)
    questions = FormQuestion.objects.filter(event=event, form=FormQuestion.Form.SPEAKER_INFO).order_by("sort_order")
    saved = es.form_responses or {}
    initial = {f"q_{k}": v for k, v in saved.items()}
    initial["recording_consent"] = {True: "yes", False: "no"}.get(es.recording_consent, "")
    form = EventInfoForm(request.POST or None, questions=questions, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            answers = validate_answers(event, form.answers(), form=FormQuestion.Form.SPEAKER_INFO)
        except ValidationError as exc:
            for slug, errs in exc.message_dict.items():
                form.add_error(f"q_{slug}" if f"q_{slug}" in form.fields else None, errs)
        else:
            es.form_responses = {**saved, **answers}
            es.recording_consent = form.cleaned_data["recording_consent"]
            es.save(update_fields=["form_responses", "recording_consent", "updated_at"])
            messages.success(request, f"Saved your details for {event.name}.")
            return redirect("portal:sessions")
    return render(request, "portal/event_info.html", _ctx(request, event=event, form=form))
