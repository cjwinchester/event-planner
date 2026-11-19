"""Link a logging-in user to the company-contact records staff created for them.

Staff add a contact (name + email) to a company; when that person logs in
with the same *verified* email, the contact is linked to their account and
the company appears in their portal. Same rule as speaker profiles
(speakers.merging.claim_profiles): unverified addresses never claim anything.
"""

from __future__ import annotations

from django.db.models.functions import Lower

from .models import CompanyContact


def claim_contacts(user) -> int:
    from allauth.account.models import EmailAddress

    emails = {e.lower() for e in EmailAddress.objects.filter(user=user, verified=True).values_list("email", flat=True)}
    if not emails:
        return 0
    return (
        CompanyContact.objects.annotate(lower_email=Lower("email"))
        .filter(user__isnull=True, lower_email__in=emails)
        .update(user=user)
    )


def my_contacts(user):
    """The user's contact rows (one per company), with the company."""
    return CompanyContact.objects.filter(user=user).select_related("company").order_by("company__name")
