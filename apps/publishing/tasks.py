from django.tasks import task


@task
def publish_event_task(event_id: int, formats: list[str] | None = None, user_id: int | None = None) -> int:
    from django.contrib.auth import get_user_model

    from apps.events.models import Event

    from .services import publish_event

    user = get_user_model().objects.filter(pk=user_id).first() if user_id else None
    return publish_event(Event.objects.get(pk=event_id), formats=formats, user=user).pk
