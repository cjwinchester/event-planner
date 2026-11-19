"""Hook every session save/delete (grid, panels, admin, API, speaker portal,
imports) so changes after the cutoff reach the hotel's change list. All
scheduling writes go through Session.save(), so nothing bypasses this."""

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from apps.program.models import Session

from .changes import board_state, record_change, tracking


@receiver(pre_save, sender=Session)
def remember_board_state(sender, instance, raw=False, **kwargs):
    instance._hotel_before = None
    instance._hotel_tracking = False
    if raw or not instance.event_id or not tracking(instance.event):
        return
    instance._hotel_tracking = True
    if instance.pk:
        old = Session.objects.select_related("room__room").filter(pk=instance.pk).first()
        instance._hotel_before = board_state(old)


@receiver(post_save, sender=Session)
def record_board_change(sender, instance, raw=False, **kwargs):
    if raw or not getattr(instance, "_hotel_tracking", False):
        return
    record_change(instance.event, instance.pk, instance._hotel_before, board_state(instance))


@receiver(post_delete, sender=Session)
def record_board_removal(sender, instance, origin=None, **kwargs):
    # Only when sessions themselves are deleted, not when their whole event is.
    if not isinstance(origin, Session) and getattr(origin, "model", None) is not Session:
        return
    if tracking(instance.event):
        record_change(instance.event, instance.pk, board_state(instance), None)
