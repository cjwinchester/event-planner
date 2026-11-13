"""Pitch review dashboard (/plan/<org>/<event>/pitches/).

Replaces the SuperGrid review tab: one row per pitch sent to the event, a
vote per staffer, notes, tracks, and the queue → finalize flow from
pitches.services.

Rules agreed with the project owner:
  * Blind review — you see others' votes on a pitch only after voting on it
    yourself, or once a decision is queued/made (services.votes_visible).
  * Staff vote, comment, assign and queue decisions; only org admins
    finalize (services.inform). Pitchers aren't notified yet.
  * "Combo" is the house term for merging pitches.
  * Staff can enter a pitch on someone's behalf (no window; sharing granted
    only as ENGAGEMENT, so a speaker's revocation stands).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from apps.organizations.models import Membership
from apps.organizations.permissions import has_role
from apps.pitches import services
from apps.pitches.models import FormQuestion, Pitch, PitchTarget, PitchVote
from apps.program.models import SessionSpeaker, SessionType, SkillLevel, Track
from apps.speakers.models import SpeakerProfile, SpeakerShareGrant

from .access import Role, event_view, require_event_role

Status = PitchTarget.Status
SORTS = {"newest": "Newest", "oldest": "Oldest", "support": "Most support", "title": "Title"}
BULK_ACTIONS = {
    "queue_accept": "Queue: accept",
    "queue_decline": "Queue: decline",
    "waitlist": "Waitlist",
    "reset": "Back to undecided",
    "assign": "Assign to…",
    "combo": "Combo into…",
}


@dataclass
class Row:
    target: PitchTarget
    my_vote: PitchVote | None
    visible: bool
    votes: list = field(default_factory=list)

    @property
    def tally(self) -> dict[str, int]:
        counts = {v: 0 for v in PitchVote.Vote.values}
        for v in self.votes:
            counts[v.vote] += 1
        return counts

    @property
    def support(self) -> int:
        t = self.tally
        return t["yes"] - t["no"]

    @property
    def voters(self) -> list[tuple[str, str]]:
        return [(_initials(v.user), v.vote) for v in self.votes]


def _initials(user) -> str:
    name = user.get_full_name() or user.email.split("@")[0]
    parts = [p for p in name.replace(".", " ").split() if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


def _row(target: PitchTarget, user) -> Row:
    votes = list(target.votes.all())
    mine = next((v for v in votes if v.user_id == user.pk), None)
    return Row(target, mine, services.votes_visible(target, user, has_voted=mine is not None), votes)


def _targets(event=None):
    qs = PitchTarget.objects.all() if event is None else PitchTarget.objects.filter(event=event)
    return qs.select_related("pitch__submitted_by", "assigned_to", "session", "merged_into__pitch").prefetch_related(
        "votes__user", "suggested_tracks", "pitch__pitch_speakers__speaker"
    )


def _filtered_rows(request, event) -> tuple[list[Row], dict]:
    # Bulk actions POST the current filters along (hx-include) so the table
    # re-renders with the same view.
    params = request.GET if request.method == "GET" else request.POST
    f = {
        "status": params.get("status", "open"),
        "track": params.get("track", ""),
        "format": params.get("format", ""),
        "q": params.get("q", "").strip(),
        "mine": params.get("mine") == "1",
        "unvoted": params.get("unvoted") == "1",
        "sort": params.get("sort") if params.get("sort") in SORTS else "newest",
    }
    qs = _targets(event)
    if f["status"] == "open":
        qs = qs.exclude(status__in=PitchTarget.FINAL)
    elif f["status"]:
        qs = qs.filter(status=f["status"])
    if f["track"]:
        qs = qs.filter(suggested_tracks__slug=f["track"])
    if f["format"]:
        qs = qs.filter(pitch__format__iexact=f["format"])
    if f["q"]:
        qs = qs.filter(
            Q(pitch__title__icontains=f["q"])
            | Q(pitch__description__icontains=f["q"])
            | Q(pitch__submitted_by__last_name__icontains=f["q"])
            | Q(pitch__submitted_by__first_name__icontains=f["q"])
            | Q(pitch__submitted_by__affiliation__icontains=f["q"])
        )
    if f["mine"]:
        qs = qs.filter(assigned_to=request.user)
    rows = [_row(t, request.user) for t in qs.distinct()]
    if f["unvoted"]:
        rows = [r for r in rows if r.my_vote is None and r.target.status == Status.SUBMITTED]
    newest = lambda r: r.target.pitch.submitted_at  # noqa: E731
    if f["sort"] == "oldest":
        rows.sort(key=newest)
    elif f["sort"] == "title":
        rows.sort(key=lambda r: r.target.pitch.title.lower())
    elif f["sort"] == "support":
        # Blind rows can't be ranked without leaking the tally: they go last.
        rows.sort(key=newest, reverse=True)
        rows.sort(key=lambda r: (not r.visible, -r.support if r.visible else 0))
    else:
        rows.sort(key=newest, reverse=True)
    return rows, f


def _counts(event, user) -> dict:
    statuses = list(PitchTarget.objects.filter(event=event).values_list("status", flat=True))
    voted = set(PitchVote.objects.filter(target__event=event, user=user).values_list("target_id", flat=True))
    open_ids = PitchTarget.objects.filter(event=event, status=Status.SUBMITTED).values_list("pk", flat=True)
    return {
        "by_status": [
            (label, statuses.count(value), value) for value, label in Status.choices if statuses.count(value)
        ],
        "total": len(statuses),
        "unvoted": sum(1 for pk in open_ids if pk not in voted),
        "queued": sum(1 for s in statuses if s in PitchTarget.QUEUED),
    }


def _table_context(request, event, **extra):
    rows, filters = _filtered_rows(request, event)
    return {
        "event": event,
        "rows": rows,
        "filters": filters,
        "counts": _counts(event, request.user),
        "statuses": Status.choices,
        "sorts": SORTS,
        "tracks": event.tracks.order_by("sort_order", "name"),
        "formats": sorted(
            {f for f in Pitch.objects.filter(targets__event=event).values_list("format", flat=True) if f}
        ),
        "bulk_actions": BULK_ACTIONS,
        "staff_members": _staff(event),
        "can_edit": request.can_edit,
        "is_admin": has_role(request.user, event.organization, Role.ADMIN),
        "votes": PitchVote.Vote.choices,
        **extra,
    }


def _staff(event):
    return (
        get_user_model()
        .objects.filter(
            memberships__organization=event.organization,
            memberships__is_active=True,
            memberships__role__in=Membership.roles_at_least(Role.STAFF),
        )
        .distinct()
        .order_by("first_name", "email")
    )


@event_view()
def pitches(request, event):
    if request.GET.get("partial") == "counts":
        return render(request, "planner/partials/pitch_counts.html", {"counts": _counts(event, request.user)})
    ctx = _table_context(request, event)
    if request.GET.get("partial") == "table":
        return render(request, "planner/partials/pitch_table.html", ctx)
    return render(request, "planner/pitches.html", ctx)


# ------------------------------------------------------------------ single pitch


def _target_for(request, pk, role=Role.VIEWER) -> PitchTarget:
    target = get_object_or_404(_targets_base(), pk=pk)
    require_event_role(request.user, target.event, role)
    request.can_edit = has_role(request.user, target.event.organization, Role.STAFF)
    return target


def _targets_base():
    return PitchTarget.objects.select_related("event__organization", "pitch__submitted_by", "assigned_to", "session")


def _row_response(request, target, status=200):
    target = _targets().get(pk=target.pk)
    return render(
        request,
        "planner/partials/pitch_row.html",
        {"row": _row(target, request.user), "can_edit": request.can_edit, "votes": PitchVote.Vote.choices},
        status=status,
    )


@login_required
@require_POST
def pitch_vote(request, pk):
    target = _target_for(request, pk, Role.STAFF)
    comment = request.POST.get("comment")
    services.cast_vote(target, request.user, request.POST.get("vote", ""), comment)
    if request.POST.get("from") == "panel":
        return _panel(request, target, message="Vote saved.")
    response = _row_response(request, target)
    response["HX-Trigger"] = "pitches:counts"
    return response


def _history(target) -> dict:
    org = target.event.organization
    speaker_ids = [target.pitch.submitted_by_id, *[ps.speaker_id for ps in target.pitch.pitch_speakers.all()]]
    sessions = (
        SessionSpeaker.objects.filter(speaker_id__in=speaker_ids, session__event__organization=org)
        .exclude(session__status="canceled")
        .exclude(session__event=target.event)
        .select_related("speaker", "session__event")
        .order_by("-session__event__start_date")[:20]
    )
    other_pitches = (
        PitchTarget.objects.filter(event__organization=org, pitch__submitted_by_id=target.pitch.submitted_by_id)
        .exclude(pk=target.pk)
        .select_related("pitch", "event")
        .order_by("-pitch__submitted_at")[:10]
    )
    return {"sessions": sessions, "other_pitches": other_pitches}


def _answer_text(value) -> str:
    if isinstance(value, list):
        return ", ".join(map(str, value))
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _panel(request, target, *, message="", errors=(), status=200):
    target = _targets().get(pk=target.pk)
    row = _row(target, request.user)
    questions = {
        q.slug: q.prompt for q in FormQuestion.objects.filter(event=target.event, form=FormQuestion.Form.PITCH)
    }
    answers = [(questions.get(k, k), _answer_text(v)) for k, v in (target.answers or {}).items()]
    return render(
        request,
        "planner/partials/pitch_panel.html",
        {
            "row": row,
            "target": target,
            "pitch": target.pitch,
            "answers": answers,
            "history": _history(target),
            "tracks": target.event.tracks.order_by("sort_order", "name"),
            "selected_tracks": {t.pk for t in target.suggested_tracks.all()},
            "staff_members": _staff(target.event),
            "can_edit": request.can_edit,
            "votes": PitchVote.Vote.choices,
            "message": message,
            "errors": errors,
        },
        status=status,
    )


@login_required
def pitch_panel(request, pk):
    target = _target_for(request, pk)
    if request.method != "POST":
        return _panel(request, target)
    require_event_role(request.user, target.event, Role.STAFF)
    target.staff_notes = request.POST.get("staff_notes", target.staff_notes)
    assignee = request.POST.get("assigned_to")
    target.assigned_to = _staff(target.event).filter(pk=assignee).first() if assignee else None
    target.save(update_fields=["staff_notes", "assigned_to", "updated_at"])
    target.suggested_tracks.set(Track.objects.filter(event=target.event, pk__in=request.POST.getlist("tracks")))
    response = _panel(request, target, message="Saved.")
    response["HX-Trigger"] = "pitches:refresh"
    return response


# ------------------------------------------------------------------ bulk + finalize


@event_view(Role.STAFF)
def pitch_bulk(request, event):
    if request.method != "POST":
        raise PermissionDenied
    ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
    targets = list(_targets().filter(event=event, pk__in=ids))
    action = request.POST.get("action", "")
    message, error = "", ""
    try:
        if not targets:
            raise ValidationError("Select at least one pitch.")
        if action in ("queue_accept", "queue_decline", "waitlist", "reset"):
            decision = {
                "queue_accept": Status.ACCEPT_QUEUE,
                "queue_decline": Status.DECLINE_QUEUE,
                "waitlist": Status.WAITLISTED,
                "reset": Status.SUBMITTED,
            }[action]
            n = services.queue_decision(targets, decision, request.user)
            message = f"{BULK_ACTIONS[action].replace('Queue: ', 'Queued ')} — {n} pitch(es)."
        elif action == "assign":
            assignee = _staff(event).filter(pk=request.POST.get("assignee") or 0).first()
            PitchTarget.objects.filter(pk__in=[t.pk for t in targets]).update(assigned_to=assignee)
            message = f"Assigned {len(targets)} pitch(es) to {assignee or 'nobody'}."
        elif action == "combo":
            primary = next((t for t in targets if str(t.pk) == request.POST.get("primary")), None)
            if primary is None:
                raise ValidationError("Pick which selected pitch the others combo into.")
            n = services.combo(primary, [t for t in targets if t.pk != primary.pk], request.user)
            message = f"Combo'd {n} pitch(es) into “{primary.pitch.title}”."
        else:
            raise ValidationError("Choose an action.")
    except ValidationError as exc:
        error = " ".join(exc.messages)
    ctx = _table_context(request, event, message=message, error=error)
    response = render(request, "planner/partials/pitch_table.html", ctx, status=200 if not error else 422)
    return response


@event_view(Role.STAFF)
def pitch_finalize(request, event):
    queued = list(_targets().filter(event=event, status__in=PitchTarget.QUEUED))
    is_admin = has_role(request.user, event.organization, Role.ADMIN)
    result = None
    if request.method == "POST":
        if not is_admin:
            raise PermissionDenied("Only organization admins can finalize decisions.")
        with transaction.atomic():
            result = services.inform(queued)
        queued = []
    accepts = [t for t in queued if t.status == Status.ACCEPT_QUEUE]
    declines = [t for t in queued if t.status == Status.DECLINE_QUEUE]
    return render(
        request,
        "planner/pitch_finalize.html",
        {
            "event": event,
            "accepts": accepts,
            "declines": declines,
            "total": len(accepts) + len(declines),
            "is_admin": is_admin,
            "result": result,
        },
    )


# ------------------------------------------------------------------ staff-entered pitch


@event_view(Role.STAFF)
def pitch_new(request, event):
    errors: list[str] = []
    data = request.POST if request.method == "POST" else {}
    if request.method == "POST":
        speaker = _speaker_from_form(request, event, errors)
        title, description = data.get("title", "").strip(), data.get("description", "").strip()
        if not title or not description:
            errors.append("Title and description are required.")
        if not errors:
            with transaction.atomic():
                pitch = services.submit_pitch(
                    submitter=speaker,
                    title=title,
                    description=description,
                    events=[event],
                    user=request.user,
                    on_behalf=True,
                    format=data.get("format", ""),
                    skill_level=data.get("skill_level", ""),
                    speaker_suggestions=data.get("speaker_suggestions", ""),
                    willing_to_organize=False,
                )
                target = pitch.targets.get()
                target.staff_notes = data.get("staff_notes", "")
                target.assigned_to = request.user
                target.save(update_fields=["staff_notes", "assigned_to", "updated_at"])
                target.suggested_tracks.set(Track.objects.filter(event=event, pk__in=request.POST.getlist("tracks")))
            response = _panel(request, target, message="Pitch added on the speaker's behalf.")
            response["HX-Trigger"] = "pitches:refresh"
            return response
    return render(
        request,
        "planner/partials/pitch_new.html",
        {
            "event": event,
            "data": data,
            "errors": errors,
            "tracks": event.tracks.order_by("sort_order", "name"),
            "formats": SessionType.objects.filter(organization=event.organization, is_active=True),
            "skill_levels": SkillLevel.objects.filter(organization=event.organization),
        },
        status=422 if errors else 200,
    )


def _speaker_from_form(request, event, errors) -> SpeakerProfile | None:
    """An existing profile (picked from search, or matched by email), else a
    new one created by this org."""
    data = request.POST
    picked = data.get("speaker")
    if picked:
        return get_object_or_404(SpeakerProfile, pk=picked, merged_into__isnull=True)
    email = data.get("email", "").strip()
    if email:
        existing = SpeakerProfile.objects.filter(email__iexact=email, merged_into__isnull=True).first()
        if existing:
            return existing
    first = data.get("first_name", "").strip()
    if not first:
        errors.append("Pick an existing speaker or enter at least a first name for a new one.")
        return None
    from apps.speakers import sharing

    profile = SpeakerProfile.objects.create(
        first_name=first,
        last_name=data.get("last_name", "").strip(),
        email=email,
        affiliation=data.get("affiliation", "").strip(),
        created_by_organization=event.organization,
    )
    sharing.grant(profile, event.organization, SpeakerShareGrant.Source.CREATED)
    return profile


@event_view(Role.STAFF)
def pitch_speaker_search(request, event):
    q = request.GET.get("q", "").strip()
    results = []
    if len(q) >= 2:
        terms = Q()
        for part in q.split():
            terms &= Q(first_name__icontains=part) | Q(last_name__icontains=part) | Q(affiliation__icontains=part)
        results = SpeakerProfile.objects.filter(terms, merged_into__isnull=True).order_by("last_name")[:10]
    return render(request, "planner/partials/pitch_speaker_pick.html", {"results": results, "q": q})
