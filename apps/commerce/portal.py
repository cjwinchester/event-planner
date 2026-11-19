"""What a company sees and may do in the portal, per event.

* Deals at the PROSPECT stage are internal and never shown to the company.
* Status reads as the company would put it: committed/contracted are
  "Confirmed"; then "Invoiced", "Paid" or "Canceled".
* The company sees its own amounts and invoices (with the payment link).
* Until the event's `company_edit_deadline` (or, if blank, the event's
  start), a managing contact may upload deliverable files and answer the
  event's exhibitor questions.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from .models import DealStatus, ExhibitorBooking, Invoice, Sponsorship, SponsorshipDeliverable

STATUS_LABELS = {
    DealStatus.COMMITTED: "Confirmed",
    DealStatus.CONTRACTED: "Confirmed",
    DealStatus.INVOICED: "Invoiced",
    DealStatus.PAID: "Paid",
    DealStatus.CANCELED: "Canceled",
}


class CompanyPortalError(ValueError):
    pass


def deadline(event):
    return event.company_edit_deadline or event.starts_at


def can_edit(event, now=None) -> bool:
    return (now or timezone.now()) <= deadline(event)


def sponsorships(company):
    return (
        Sponsorship.objects.filter(company=company)
        .exclude(status=DealStatus.PROSPECT)
        .select_related("event__organization", "level", "track", "session")
        .prefetch_related("deliverables")
        .order_by("-event__start_date", "pk")
    )


def bookings(company):
    return (
        ExhibitorBooking.objects.filter(company=company)
        .exclude(status=DealStatus.PROSPECT)
        .select_related("event__organization", "package", "booth_room__room")
        .order_by("-event__start_date", "pk")
    )


def invoices_for(deal) -> list[Invoice]:
    field = "sponsorship" if isinstance(deal, Sponsorship) else "booking"
    return list(
        Invoice.objects.filter(**{field: deal}).exclude(status=Invoice.Status.DRAFT).order_by("issued_at", "pk")
    )


def upload_deliverable(item: SponsorshipDeliverable, upload) -> None:
    event = item.sponsorship.event
    if not can_edit(event):
        raise CompanyPortalError("The deadline for sponsor materials has passed. Contact the organizers.")
    if item.status == SponsorshipDeliverable.Status.DONE:
        raise CompanyPortalError("The organizers already marked this one done.")
    with transaction.atomic():
        item.file = upload
        item.status = SponsorshipDeliverable.Status.RECEIVED
        item.save(update_fields=["file", "status", "updated_at"])


def save_exhibitor_answers(booking: ExhibitorBooking, answers: dict) -> None:
    from apps.pitches.models import FormQuestion
    from apps.pitches.services import validate_answers

    if not can_edit(booking.event):
        raise CompanyPortalError("The deadline for exhibitor details has passed. Contact the organizers.")
    cleaned = validate_answers(booking.event, answers, form=FormQuestion.Form.EXHIBITOR)
    booking.answers = {**booking.answers, **cleaned}
    booking.save(update_fields=["answers", "updated_at"])
