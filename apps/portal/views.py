"""Portal (/portal/), speaking side: a speaker's own profile, who may see their
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

from .forms import (
    CoSpeakerFormSet,
    EventInfoForm,
    GrantForm,
    LinkFormSet,
    PitchForm,
    ProfileForm,
    SessionEditForm,
    WithdrawForm,
)


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
    companies = []
    if request.user.is_authenticated:
        from apps.commerce.claiming import my_contacts

        companies = [c.company for c in my_contacts(request.user)]
    return {"is_staff_member": bool(organization_ids_for(request.user)), "my_companies": companies, **extra}


@login_required
def portal_home(request):
    """Everything this person does with us: speaking (if they have a speaker
    profile) and each company they're a contact for. Either can be empty."""
    from apps.commerce.claiming import claim_contacts

    profile = _profile(request)
    claim_contacts(request.user)
    ctx = _ctx(request, profile=profile)
    if profile is None and not ctx["my_companies"]:
        return redirect("portal:profile")  # a brand-new account: start with a speaker profile
    if profile is not None:
        grants = profile.share_grants.select_related("organization")
        missing = [
            label for f, label in (("bio", "a bio"), ("affiliation", "your affiliation")) if not getattr(profile, f)
        ]
        if not profile.headshot_href:
            missing.append("a headshot")
        ctx.update(
            public=sharing.visible_profile(profile),
            active_grants=[g for g in grants if g.is_active],
            missing=missing,
        )
    return render(request, "portal/home.html", ctx)


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


# ------------------------------------------------------------------ part C: open calls and pitches

STATUS_LABELS = {
    "under_review": "Under review",
    "accepted": "Accepted",
    "not_accepted": "Not accepted",
    "combined": "Combined with a similar pitch",
    "withdrawn": "Withdrawn",
}


def _open_calls():
    from apps.core.models import Visibility
    from apps.events.models import Event

    return (
        Event.objects.with_open_pitch_window()
        .exclude(visibility=Visibility.PRIVATE)
        .select_related("organization")
        .order_by("pitch_window_closes_at", "name")
    )


def calls(request):
    """Public: every event currently accepting pitches."""
    return render(request, "portal/calls.html", _ctx(request, events=_open_calls()))


def _co_speakers(formset, submitter) -> list[SpeakerProfile]:
    """Find co-speakers by email or create profiles for them. Nothing about them is
    shared with organizers until they log in and choose to share."""
    from django.db.models.functions import Lower

    found = []
    for f in formset:
        data = getattr(f, "cleaned_data", None) or {}
        email = (data.get("email") or "").strip()
        if not email:
            continue
        profile = (
            SpeakerProfile.objects.annotate(lower_email=Lower("email"))
            .filter(lower_email=email.lower(), merged_into__isnull=True)
            .first()
        )
        if profile is None:
            profile = SpeakerProfile.objects.create(
                first_name=data["first_name"], last_name=data.get("last_name", ""), email=email
            )
        if profile.pk != submitter.pk and profile not in found:
            found.append(profile)
    return found


def _datalists(events):
    from apps.program.models import SessionType, SkillLevel

    org_ids = {e.organization_id for e in events}
    return {
        "formats": SessionType.objects.filter(organization_id__in=org_ids).values_list("name", flat=True).distinct(),
        "levels": SkillLevel.objects.filter(organization_id__in=org_ids).values_list("name", flat=True).distinct(),
    }


@login_required
def pitch_new(request):
    from apps.pitches import services as pitch_services

    profile = _profile(request)
    if profile is None:
        messages.info(request, "Create your speaker profile first; organizers see it with your pitch.")
        return redirect("portal:profile")
    open_calls = list(_open_calls())
    chosen_ids = {int(x) for x in (request.POST or request.GET).getlist("events") if x.isdigit()}
    events = [e for e in open_calls if e.pk in chosen_ids]
    if not events:
        return render(request, "portal/pitch_choose.html", _ctx(request, events=open_calls))
    form = PitchForm(request.POST or None, events=events)
    co = CoSpeakerFormSet(request.POST or None, prefix="co")
    if request.method == "POST" and form.is_valid() and co.is_valid():
        data = form.cleaned_data
        try:
            pitch = pitch_services.submit_pitch(
                submitter=profile,
                user=request.user,
                title=data["title"],
                description=data["description"],
                events=events,
                co_speakers=_co_speakers(co, profile),
                answers_by_event=form.answers_by_event(),
                format=data["format"],
                skill_level=data["skill_level"],
                speaker_suggestions=data["speaker_suggestions"],
                willing_to_organize=data["willing_to_organize"],
            )
        except ValidationError as exc:
            _form_errors(form, exc)
        except pitch_services.PitchWorkflowError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Pitch sent. You'll see decisions here once the organizers make them.")
            return redirect("portal:pitch", pk=pitch.pk)
    return render(
        request,
        "portal/pitch_form.html",
        _ctx(request, form=form, co=co, events=events, creating=True, **_datalists(events)),
        status=422 if request.method == "POST" else 200,
    )


def _form_errors(form, exc: ValidationError) -> None:
    """validate_answers raises {slug: msg}; show it on every event's matching field."""
    for slug, errs in exc.message_dict.items():
        placed = False
        for _event, names in form.event_questions:
            for name, s in names:
                if s == slug:
                    form.add_error(name, errs)
                    placed = True
        if not placed:
            form.add_error(None, errs)


def _my_pitches(profile):
    from django.db.models import Q

    from apps.pitches.models import Pitch

    return (
        Pitch.objects.filter(Q(submitted_by=profile) | Q(pitch_speakers__speaker=profile))
        .distinct()
        .select_related("submitted_by")
        .prefetch_related("targets__event__organization", "targets__merged_into", "pitch_speakers__speaker")
        .order_by("-submitted_at")
    )


def _pitch_rows(pitch):
    from apps.pitches import services as pitch_services

    return [
        {"target": t, "status": (s := pitch_services.speaker_status(t)), "label": STATUS_LABELS[s]}
        for t in pitch.targets.all()
    ]


@login_required
def pitches(request):
    profile = _profile(request)
    if profile is None:
        return redirect("portal:profile")
    rows = [
        {"pitch": p, "targets": _pitch_rows(p), "mine": p.submitted_by_id == profile.pk} for p in _my_pitches(profile)
    ]
    return render(request, "portal/pitches.html", _ctx(request, rows=rows, open_count=_open_calls().count()))


@login_required
def pitch_detail(request, pk):
    from apps.pitches import services as pitch_services

    profile = _profile(request)
    pitch = get_object_or_404(_my_pitches(profile), pk=pk) if profile else None
    if pitch is None:
        return redirect("portal:profile")
    mine = pitch.submitted_by_id == profile.pk  # co-speakers get a read-only view
    if request.method == "POST" and mine:
        target = get_object_or_404(pitch.targets, pk=request.POST.get("target"))
        try:
            if request.POST.get("action") == "withdraw":
                pitch_services.speaker_withdraw(target)
                messages.success(request, f"Withdrawn from {target.event.name}.")
            elif request.POST.get("action") == "confirm":
                pitch_services.confirm(target)
                messages.success(request, "Thanks for confirming! See it under Sessions.")
        except pitch_services.PitchWorkflowError as exc:
            messages.error(request, str(exc))
        return redirect("portal:pitch", pk=pitch.pk)
    return render(
        request,
        "portal/pitch.html",
        _ctx(
            request,
            pitch=pitch,
            mine=mine,
            targets=_pitch_rows(pitch),
            can_edit=mine and pitch_services.speaker_can_edit(pitch),
            co_speakers=[ps.speaker for ps in pitch.pitch_speakers.all() if ps.speaker_id != pitch.submitted_by_id],
        ),
    )


@login_required
def pitch_edit(request, pk):
    from apps.pitches import services as pitch_services

    profile = _profile(request)
    if profile is None:
        return redirect("portal:profile")
    pitch = get_object_or_404(_my_pitches(profile).filter(submitted_by=profile), pk=pk)
    if not pitch_services.speaker_can_edit(pitch):
        messages.error(request, "This pitch can't be edited any more: a pitch window closed or a decision was made.")
        return redirect("portal:pitch", pk=pitch.pk)
    targets = pitch_services.live_targets(pitch)
    events = [t.event for t in targets]
    initial = {f: getattr(pitch, f) for f in pitch_services.SPEAKER_FIELDS}
    for t in targets:
        initial.update({f"e{t.event_id}_{slug}": v for slug, v in (t.answers or {}).items()})
    co_initial = [
        {"first_name": ps.speaker.first_name, "last_name": ps.speaker.last_name, "email": ps.speaker.email}
        for ps in pitch.pitch_speakers.all()
        if ps.speaker_id != pitch.submitted_by_id
    ]
    form = PitchForm(request.POST or None, events=events, initial=initial)
    co = CoSpeakerFormSet(request.POST or None, prefix="co", initial=co_initial)
    if request.method == "POST" and form.is_valid() and co.is_valid():
        try:
            pitch_services.speaker_update_pitch(
                pitch,
                {f: form.cleaned_data[f] for f in pitch_services.SPEAKER_FIELDS},
                answers_by_event=form.answers_by_event(),
                co_speakers=_co_speakers(co, profile),
            )
        except ValidationError as exc:
            _form_errors(form, exc)
        except pitch_services.PitchWorkflowError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Pitch updated.")
            return redirect("portal:pitch", pk=pitch.pk)
    return render(
        request,
        "portal/pitch_form.html",
        _ctx(request, form=form, co=co, events=events, pitch=pitch, creating=False, **_datalists(events)),
        status=422 if request.method == "POST" else 200,
    )
