"""Link a logging-in user to their speaker profile(s) by verified email.

Runs on every login (email code, password or Google) so profiles that staff
created or imported since the last visit are picked up and duplicates merged
(speakers.merging.claim_profiles)."""

from allauth.account.signals import user_logged_in
from django.dispatch import receiver

from apps.speakers.merging import claim_profiles


@receiver(user_logged_in)
def claim_on_login(sender, request, user, **kwargs):
    claim_profiles(user)
