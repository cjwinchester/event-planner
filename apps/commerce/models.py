"""Sponsors, exhibitors, comp-ticket entitlements and payment stubs.

- `Company` is global (one company sponsors several nonprofits) and can be
  self-managed by its portal users (`CompanyContact.user`).
- Everything commercial about a specific event is org-private:
  `Sponsorship`, `ExhibitorBooking`, `Invoice`, ticket assignments.
- Sponsorship can target the whole event, a track, a session, or a special
  event (a session such as a banquet/awards dinner).
- Comp tickets: a sponsorship/booking is entitled to N tickets
  (`comp_tickets`, defaulting from its level/package); each named ticket is
  a `CompTicket` (booth staff are tickets with role BOOTH_STAFF).
- Payments: `commerce.payments.PaymentProvider` is a stub interface;
  `Invoice` records what was billed and its provider status.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import OrgScopedManager, TimeStampedModel


class Company(TimeStampedModel):
    """A sponsor and/or exhibitor organization (global, self-service)."""

    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    website = models.URLField(blank=True)
    description = models.TextField(blank=True, help_text="Public blurb.")
    logo = models.ImageField(upload_to="companies/logos/", blank=True)
    logo_url = models.URLField(blank=True)
    created_by_organization = models.ForeignKey(
        "organizations.Organization", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "companies"

    def __str__(self) -> str:
        return self.name

    @property
    def logo_href(self) -> str:
        return self.logo.url if self.logo else self.logo_url


class CompanyContact(TimeStampedModel):
    """A person at a company; with `user` set they can use the portal."""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="contacts")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="company_contacts"
    )
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=64, blank=True)
    title = models.CharField(max_length=255, blank=True)
    is_primary = models.BooleanField(default=False)
    can_manage = models.BooleanField(default=True, help_text="May edit the company profile and tickets.")

    class Meta:
        ordering = ["company", "-is_primary", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.company})"


class SponsorshipLevel(models.Model):
    """An event's sponsorship tier (Diamond, Gold, ... Host)."""

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="sponsorship_levels")
    name = models.CharField(max_length=100)
    rank = models.PositiveIntegerField(default=0, help_text="Lower = more prominent.")
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    comp_tickets = models.PositiveIntegerField(default=0)
    benefits = models.TextField(blank=True)

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "rank", "name"]
        constraints = [models.UniqueConstraint(fields=["event", "name"], name="unique_sponsorship_level")]

    def __str__(self) -> str:
        return self.name


class ExhibitorPackage(models.Model):
    """An event's exhibitor package (Corporate, Nonprofit, University...)."""

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="exhibitor_packages")
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    comp_tickets = models.PositiveIntegerField(default=0, help_text="Booth staff tickets included.")
    booth_size = models.CharField(max_length=64, blank=True)
    description = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "sort_order", "name"]
        constraints = [models.UniqueConstraint(fields=["event", "name"], name="unique_exhibitor_package")]

    def __str__(self) -> str:
        return self.name


class DealStatus(models.TextChoices):
    PROSPECT = "prospect", "Prospect"
    COMMITTED = "committed", "Committed"
    CONTRACTED = "contracted", "Contracted"
    INVOICED = "invoiced", "Invoiced"
    PAID = "paid", "Paid"
    CANCELED = "canceled", "Canceled"


class Sponsorship(TimeStampedModel):
    """A company sponsoring an event, a track, a session, or a special event."""

    class Scope(models.TextChoices):
        EVENT = "event", "Whole event"
        TRACK = "track", "Track"
        SESSION = "session", "Session"
        SPECIAL_EVENT = "special_event", "Special event (banquet, reception...)"

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="sponsorships")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="sponsorships")
    level = models.ForeignKey(
        SponsorshipLevel, null=True, blank=True, on_delete=models.SET_NULL, related_name="sponsorships"
    )
    scope = models.CharField(max_length=16, choices=Scope.choices, default=Scope.EVENT)
    track = models.ForeignKey(
        "program.Track", null=True, blank=True, on_delete=models.CASCADE, related_name="sponsorships"
    )
    session = models.ForeignKey(
        "program.Session",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="sponsorships",
        help_text="For SESSION and SPECIAL_EVENT scopes.",
    )
    status = models.CharField(max_length=16, choices=DealStatus.choices, default=DealStatus.PROSPECT)
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    comp_tickets = models.PositiveIntegerField(null=True, blank=True, help_text="Blank = level default.")
    display_name = models.CharField(max_length=255, blank=True, help_text="Override the company name publicly.")
    is_public = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "level__rank", "company__name"]

    def __str__(self) -> str:
        return f"{self.company} — {self.get_scope_display()} @ {self.event}"

    def clean(self):
        needs_track = self.scope == self.Scope.TRACK
        needs_session = self.scope in (self.Scope.SESSION, self.Scope.SPECIAL_EVENT)
        if needs_track != bool(self.track_id):
            raise ValidationError({"track": "Track sponsorships need a track (and only they do)."})
        if needs_session != bool(self.session_id):
            raise ValidationError({"session": "Session/special-event sponsorships need a session (only they do)."})
        if self.track_id and self.track.event_id != self.event_id:
            raise ValidationError({"track": "Track is from another event."})
        if self.session_id and self.session.event_id != self.event_id:
            raise ValidationError({"session": "Session is from another event."})
        if self.level_id and self.level.event_id != self.event_id:
            raise ValidationError({"level": "Level is from another event."})

    @property
    def ticket_entitlement(self) -> int:
        if self.comp_tickets is not None:
            return self.comp_tickets
        return self.level.comp_tickets if self.level_id else 0


class SponsorshipDeliverable(TimeStampedModel):
    """Something owed by or to a sponsor (logo files, ad copy, signage)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RECEIVED = "received", "Received"
        DONE = "done", "Done"

    sponsorship = models.ForeignKey(Sponsorship, on_delete=models.CASCADE, related_name="deliverables")
    title = models.CharField(max_length=255)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    file = models.FileField(upload_to="sponsorships/deliverables/", blank=True)
    notes = models.TextField(blank=True)

    ORG_PATH = "sponsorship__event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["sponsorship", "due_date"]

    def __str__(self) -> str:
        return self.title


class ExhibitorBooking(TimeStampedModel):
    """A company exhibiting at an event."""

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="exhibitor_bookings")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="exhibitor_bookings")
    package = models.ForeignKey(
        ExhibitorPackage, null=True, blank=True, on_delete=models.SET_NULL, related_name="bookings"
    )
    status = models.CharField(max_length=16, choices=DealStatus.choices, default=DealStatus.PROSPECT)
    booth_number = models.CharField(max_length=32, blank=True)
    booth_room = models.ForeignKey(
        "events.EventRoom", null=True, blank=True, on_delete=models.SET_NULL, related_name="exhibitor_bookings"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    comp_tickets = models.PositiveIntegerField(null=True, blank=True, help_text="Blank = package default.")
    is_public = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "company__name"]
        constraints = [models.UniqueConstraint(fields=["event", "company"], name="unique_exhibitor_per_event")]

    def __str__(self) -> str:
        return f"{self.company} @ {self.event}"

    def clean(self):
        if self.package_id and self.package.event_id != self.event_id:
            raise ValidationError({"package": "Package is from another event."})

    @property
    def ticket_entitlement(self) -> int:
        if self.comp_tickets is not None:
            return self.comp_tickets
        return self.package.comp_tickets if self.package_id else 0


class CompTicket(TimeStampedModel):
    """A named complimentary ticket drawn from a sponsorship or booking."""

    class Role(models.TextChoices):
        BOOTH_STAFF = "booth_staff", "Booth staff"
        GUEST = "guest", "Guest"
        SPEAKER = "speaker", "Sponsored speaker"

    sponsorship = models.ForeignKey(
        Sponsorship, null=True, blank=True, on_delete=models.CASCADE, related_name="tickets"
    )
    booking = models.ForeignKey(
        ExhibitorBooking, null=True, blank=True, on_delete=models.CASCADE, related_name="tickets"
    )
    event = models.ForeignKey(
        "events.Event",
        on_delete=models.CASCADE,
        related_name="comp_tickets",
        editable=False,
        help_text="Copied from the sponsorship/booking on save (for org scoping and queries).",
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.GUEST)
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    registration_external_id = models.CharField(
        max_length=255, blank=True, help_text="Registration created in the registration system, if any."
    )
    issued_at = models.DateTimeField(null=True, blank=True)

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(sponsorship__isnull=False, booking__isnull=True)
                    | models.Q(sponsorship__isnull=True, booking__isnull=False)
                ),
                name="comp_ticket_has_one_source",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_role_display()})"

    @property
    def source(self):
        return self.sponsorship or self.booking

    def save(self, *args, **kwargs):
        if self.source is not None:
            self.event_id = self.source.event_id
        super().save(*args, **kwargs)

    def clean(self):
        source = self.source
        if source is None:
            raise ValidationError("A ticket must come from a sponsorship or an exhibitor booking.")
        if self.sponsorship_id and self.booking_id:
            raise ValidationError("A ticket comes from a sponsorship or a booking, not both.")
        used = source.tickets.exclude(pk=self.pk).count()
        if used >= source.ticket_entitlement:
            raise ValidationError(f"All {source.ticket_entitlement} comp tickets are already assigned.")


class Invoice(TimeStampedModel):
    """What was billed for a sponsorship/booking and the payment status."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        PAID = "paid", "Paid"
        VOID = "void", "Void"

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="invoices")
    sponsorship = models.ForeignKey(
        Sponsorship, null=True, blank=True, on_delete=models.SET_NULL, related_name="invoices"
    )
    booking = models.ForeignKey(
        ExhibitorBooking, null=True, blank=True, on_delete=models.SET_NULL, related_name="invoices"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.DRAFT)
    provider = models.CharField(max_length=64, default="manual", help_text="Payment connector slug.")
    external_id = models.CharField(max_length=255, blank=True)
    payment_url = models.URLField(blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "-created_at"]

    def __str__(self) -> str:
        return f"Invoice {self.external_id or self.pk} — {self.amount}"
