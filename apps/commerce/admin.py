from django.contrib import admin

from apps.core.admin import OrgScopedAdmin, OrgScopedInline, SharedDirectoryAdmin, SharedDirectoryInline

from .models import (
    Company,
    CompanyContact,
    CompTicket,
    ExhibitorBooking,
    ExhibitorPackage,
    Invoice,
    Sponsorship,
    SponsorshipDeliverable,
    SponsorshipLevel,
)


class CompanyContactInline(SharedDirectoryInline, admin.TabularInline):
    model = CompanyContact
    extra = 0


@admin.register(Company)
class CompanyAdmin(SharedDirectoryAdmin):
    list_display = ["name", "website"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [CompanyContactInline]
    readonly_fields = ["created_by_organization"]


@admin.register(SponsorshipLevel)
class SponsorshipLevelAdmin(OrgScopedAdmin):
    list_display = ["name", "event", "rank", "price", "comp_tickets"]
    list_filter = ["event__organization", "event"]


@admin.register(ExhibitorPackage)
class ExhibitorPackageAdmin(OrgScopedAdmin):
    list_display = ["name", "event", "price", "comp_tickets", "booth_size"]
    list_filter = ["event__organization", "event"]


class DeliverableInline(OrgScopedInline, admin.TabularInline):
    model = SponsorshipDeliverable
    extra = 0


class SponsorTicketInline(OrgScopedInline, admin.TabularInline):
    model = CompTicket
    fk_name = "sponsorship"
    extra = 0
    fields = ["role", "name", "email", "registration_external_id", "issued_at"]


class BookingTicketInline(SponsorTicketInline):
    fk_name = "booking"
    verbose_name = "booth staff / comp ticket"


@admin.register(Sponsorship)
class SponsorshipAdmin(OrgScopedAdmin):
    list_display = ["company", "event", "scope", "level", "status", "amount", "tickets_used"]
    list_filter = ["event__organization", "event", "scope", "status", "level"]
    search_fields = ["company__name"]
    autocomplete_fields = ["company"]
    inlines = [DeliverableInline, SponsorTicketInline]

    @admin.display(description="Comp tickets")
    def tickets_used(self, obj):
        return f"{obj.tickets.count()} / {obj.ticket_entitlement}"


@admin.register(ExhibitorBooking)
class ExhibitorBookingAdmin(OrgScopedAdmin):
    list_display = ["company", "event", "package", "status", "booth_number", "tickets_used"]
    list_filter = ["event__organization", "event", "status", "package"]
    search_fields = ["company__name"]
    autocomplete_fields = ["company"]
    inlines = [BookingTicketInline]

    @admin.display(description="Booth staff")
    def tickets_used(self, obj):
        return f"{obj.tickets.count()} / {obj.ticket_entitlement}"


@admin.register(Invoice)
class InvoiceAdmin(OrgScopedAdmin):
    list_display = ["__str__", "event", "sponsorship", "booking", "amount", "status", "provider"]
    list_filter = ["event__organization", "event", "status", "provider"]
