"""Staff page for an event's sponsors and exhibitors: deals at a glance,
the deliverables checklist, exhibitor answers (with CSV), company contacts
and portal invites, and the sponsor/exhibitor deadline."""

from __future__ import annotations

import csv
from datetime import datetime

from django import forms
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.commerce import portal as company_portal
from apps.commerce.access import add_contact_for_org
from apps.commerce.invites import send_invite
from apps.commerce.models import Company, CompanyContact, ExhibitorBooking, Sponsorship, SponsorshipDeliverable
from apps.pitches.models import FormQuestion

from .access import Role, event_view


class ContactForm(forms.Form):
    name = forms.CharField(max_length=255, widget=forms.TextInput(attrs={"class": "input", "placeholder": "Name"}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={"class": "input", "placeholder": "Email"}))
    can_manage = forms.BooleanField(required=False, initial=True, label="Manager")


class DeliverableForm(forms.Form):
    title = forms.CharField(
        max_length=255, widget=forms.TextInput(attrs={"class": "input", "placeholder": "e.g. Logo (SVG)"})
    )
    due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date", "class": "input"}))
    notes = forms.CharField(
        required=False, widget=forms.TextInput(attrs={"class": "input", "placeholder": "Instructions"})
    )


class DeadlineForm(forms.Form):
    deadline = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "input"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M"],
    )


def _url(event):
    return reverse("planner:sponsors", args=[event.organization.slug, event.slug])


def _event_company(event, company_id) -> Company:
    """A company with a deal at this event (staff act only on those here)."""
    return get_object_or_404(
        Company.objects.filter(pk=company_id).filter(
            pk__in=list(Sponsorship.objects.filter(event=event).values_list("company_id", flat=True))
            + list(ExhibitorBooking.objects.filter(event=event).values_list("company_id", flat=True))
        )
    )


@event_view(Role.STAFF)
def sponsors(request, event):
    sponsorships = list(
        Sponsorship.objects.filter(event=event)
        .select_related("company", "level", "track", "session")
        .prefetch_related("deliverables", "company__contacts")
        .order_by("level__rank", "company__name")
    )
    bookings = list(
        ExhibitorBooking.objects.filter(event=event)
        .select_related("company", "package", "booth_room")
        .prefetch_related("company__contacts")
        .order_by("booth_number", "company__name")
    )
    questions = list(FormQuestion.objects.filter(event=event, form=FormQuestion.Form.EXHIBITOR).order_by("sort_order"))
    for b in bookings:
        b.answered = sum(1 for q in questions if (b.answers or {}).get(q.slug) not in (None, ""))
    companies = {d.company_id: d.company for d in [*sponsorships, *bookings]}
    dl = event.company_edit_deadline
    return render(
        request,
        "planner/sponsors.html",
        {
            "event": event,
            "sponsorships": sponsorships,
            "bookings": bookings,
            "questions": questions,
            "companies": sorted(companies.values(), key=lambda c: c.name.lower()),
            "contact_form": ContactForm(),
            "deliverable_form": DeliverableForm(),
            "deadline_form": DeadlineForm(
                initial={"deadline": dl.astimezone(event.tz).replace(tzinfo=None) if dl else None}
            ),
            "deadline": company_portal.deadline(event),
            "deadline_is_default": dl is None,
        },
    )


@event_view(Role.STAFF)
@require_POST
def sponsors_action(request, event):
    action = request.POST.get("action")
    if action == "add_contact":
        company = _event_company(event, request.POST.get("company"))
        form = ContactForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Give the contact a name and a valid email.")
        else:
            contact = add_contact_for_org(
                company,
                event.organization,
                name=form.cleaned_data["name"],
                email=form.cleaned_data["email"],
                manager=form.cleaned_data["can_manage"],
            )
            scope = "company-wide" if contact.company_wide else f"{event.organization.name}'s deals only"
            role = f"a manager ({scope})" if contact.can_manage else "a member (no deal access)"
            messages.success(request, f"Added {contact.name} to {company.name} as {role}.")
    elif action == "invite":
        contact = get_object_or_404(CompanyContact, pk=request.POST.get("contact"))
        _event_company(event, contact.company_id)
        if not contact.email:
            messages.error(request, f"{contact.name} has no email address.")
        else:
            send_invite(contact, event, request)
            messages.success(request, f"Sent a portal invite to {contact.email}.")
    elif action == "invite_all":
        sent = 0
        for company in [_event_company(event, cid) for cid in request.POST.getlist("company")] or []:
            for contact in company.contacts.filter(user__isnull=True, invited_at__isnull=True).exclude(email=""):
                send_invite(contact, event, request)
                sent += 1
        messages.success(request, f"Sent {sent} invite{'s' if sent != 1 else ''}.")
    elif action == "add_deliverable":
        sp = get_object_or_404(Sponsorship, pk=request.POST.get("sponsorship"), event=event)
        form = DeliverableForm(request.POST)
        if form.is_valid():
            SponsorshipDeliverable.objects.create(sponsorship=sp, **form.cleaned_data)
            messages.success(request, f"Asked {sp.company.name} for “{form.cleaned_data['title']}”.")
        else:
            messages.error(request, "Give the deliverable a title.")
    elif action in ("mark_done", "mark_pending"):
        item = get_object_or_404(SponsorshipDeliverable, pk=request.POST.get("deliverable"), sponsorship__event=event)
        item.status = (
            SponsorshipDeliverable.Status.DONE if action == "mark_done" else SponsorshipDeliverable.Status.PENDING
        )
        item.save(update_fields=["status", "updated_at"])
    elif action == "deadline":
        form = DeadlineForm(request.POST)
        if form.is_valid():
            value: datetime | None = form.cleaned_data["deadline"]
            event.company_edit_deadline = value.replace(tzinfo=event.tz) if value else None
            event.save(update_fields=["company_edit_deadline", "updated_at"])
            messages.success(
                request, "Deadline saved." if value else "Deadline cleared: companies can edit until the event starts."
            )
    return redirect(_url(event))


@event_view(Role.STAFF)
def exhibitor_answers_csv(request, event):
    questions = list(FormQuestion.objects.filter(event=event, form=FormQuestion.Form.EXHIBITOR).order_by("sort_order"))
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{event.slug}-exhibitors.csv"'
    writer = csv.writer(response)
    writer.writerow(["company", "package", "booth", "status", *[q.prompt for q in questions]])
    for b in (
        ExhibitorBooking.objects.filter(event=event).select_related("company", "package").order_by("company__name")
    ):
        writer.writerow(
            [b.company.name, b.package.name if b.package else "", b.booth_number, b.get_status_display()]
            + [(b.answers or {}).get(q.slug, "") for q in questions]
        )
    return response
