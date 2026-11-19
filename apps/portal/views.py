"""Speaker portal (/speak/): a speaker's own profile, who may see their
contact details, and (parts B/C) their sessions and pitches.

Any logged-in user may use it; it only ever shows the user's *own* speaker
profile (`SpeakerProfile.user`). Profiles are linked on login by verified
email (`speakers.merging.claim_profiles`), or created here from scratch.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.organizations.models import Organization
from apps.organizations.permissions import organization_ids_for
from apps.speakers import sharing
from apps.speakers.merging import claim_profiles
from apps.speakers.models import SpeakerProfile, SpeakerShareGrant

from .forms import GrantForm, LinkFormSet, ProfileForm


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
