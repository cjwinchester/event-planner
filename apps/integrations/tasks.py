"""Background tasks (Django Tasks; run `manage.py db_worker`)."""

from django.tasks import task


@task
def sync_event_registrations(event_id: int, user_id: int | None = None) -> int:
    from django.contrib.auth import get_user_model

    from apps.events.models import Event

    from .services import sync_registrations

    user = get_user_model().objects.filter(pk=user_id).first() if user_id else None
    return sync_registrations(Event.objects.get(pk=event_id), triggered_by=user).pk


@task
def sync_event_meetings(event_id: int, user_id: int | None = None) -> int:
    from django.contrib.auth import get_user_model

    from apps.events.models import Event

    from .meetings import sync_meetings

    user = get_user_model().objects.filter(pk=user_id).first() if user_id else None
    return sync_meetings(Event.objects.get(pk=event_id), triggered_by=user).pk


@task
def pull_event_attendance(event_id: int, user_id: int | None = None) -> int:
    from django.contrib.auth import get_user_model

    from apps.events.models import Event

    from .meetings import pull_attendance

    user = get_user_model().objects.filter(pk=user_id).first() if user_id else None
    return pull_attendance(Event.objects.get(pk=event_id), triggered_by=user).pk
