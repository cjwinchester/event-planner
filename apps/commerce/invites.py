"""Portal invites for company contacts, sent from the organization's
Workspace account (settings.MAILERS / DEFAULT_FROM_EMAIL)."""

from __future__ import annotations

from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import CompanyContact, ExhibitorBooking, Sponsorship
from .portal import deadline


def send_invite(contact: CompanyContact, event, request) -> None:
    """Email `contact` a sign-in link for the portal and record when."""
    roles = []
    if Sponsorship.objects.filter(event=event, company=contact.company).exists():
        roles.append("sponsorship")
    if ExhibitorBooking.objects.filter(event=event, company=contact.company).exists():
        roles.append("exhibit booth")
    tz = event.tz
    context = {
        "contact": contact,
        "company": contact.company,
        "event": event,
        "organization": event.organization,
        "roles": roles,
        "deadline": deadline(event).astimezone(tz).strftime("%B %-d, %-I:%M %p %Z"),
        "login_url": request.build_absolute_uri(reverse("account_request_login_code")),
    }
    subject = render_to_string("email/company_invite_subject.txt", context).strip()
    body = render_to_string("email/company_invite.txt", context)
    send_mail(subject, body, None, [contact.email])
    contact.invited_at = timezone.now()
    contact.save(update_fields=["invited_at", "updated_at"])
