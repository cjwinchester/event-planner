"""Access helpers for the staff UI: resolve an event from URL slugs and
check the user's role in its organization (same rules as admin and API)."""

from __future__ import annotations

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.events.models import Event
from apps.organizations.models import Membership
from apps.organizations.permissions import has_role

Role = Membership.Role


def event_view(min_role: str = Role.VIEWER):
    """Decorator for views taking (request, org_slug, event_slug, ...).
    Passes `event` instead of the slugs; adds `request.can_edit`. Renders in
    the event's timezone, so template date/time filters show event-local
    times (they convert aware datetimes to the *active* zone)."""

    def decorator(view):
        @login_required
        @wraps(view)
        def wrapper(request, org_slug, event_slug, *args, **kwargs):
            event = get_object_or_404(
                Event.objects.select_related("organization"), organization__slug=org_slug, slug=event_slug
            )
            if not has_role(request.user, event.organization, min_role):
                raise PermissionDenied
            request.can_edit = has_role(request.user, event.organization, Role.STAFF)
            with timezone.override(event.tz):
                return view(request, event, *args, **kwargs)

        return wrapper

    return decorator


def require_event_role(user, event, role: str = Role.STAFF) -> None:
    if not has_role(user, event.organization, role):
        raise PermissionDenied
