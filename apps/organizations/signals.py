"""Keep `User.is_staff` (Django admin access) in sync with memberships.

Phase 1 uses the Django admin as the staff UI, so any user with an active
membership needs `is_staff`. What they can see/do there is decided by
core.admin.OrgScopedAdmin from their membership roles, not Django model
permissions.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Membership


def sync_admin_access(user) -> None:
    if user.is_superuser:
        return
    should_be_staff = user.memberships.filter(is_active=True).exists()
    if user.is_staff != should_be_staff:
        user.is_staff = should_be_staff
        user.save(update_fields=["is_staff"])


@receiver(post_save, sender=Membership)
def membership_saved(sender, instance, **kwargs):
    sync_admin_access(instance.user)


@receiver(post_delete, sender=Membership)
def membership_deleted(sender, instance, **kwargs):
    sync_admin_access(instance.user)
