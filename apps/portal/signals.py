"""Link a logging-in user to their speaker profile(s) and company-contact
records by verified email.

Runs on every login (email code, password or Google) so records that staff
created or imported since the last visit are picked up and speaker duplicates
merged (speakers.merging.claim_profiles, commerce.claiming.claim_contacts).
One person can be a speaker and a contact for several companies at once."""

from allauth.account.signals import user_logged_in
from django.dispatch import receiver

from apps.commerce.claiming import claim_contacts
from apps.speakers.merging import claim_profiles


@receiver(user_logged_in)
def claim_on_login(sender, request, user, **kwargs):
    claim_profiles(user)
    claim_contacts(user)
