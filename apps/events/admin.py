from django.contrib import admin, messages
from django.utils.html import format_html, format_html_join

from apps.core.admin import EventTimezoneMixin, OrgScopedAdmin, OrgScopedInline
from apps.program.conflicts import Severity, event_conflicts
from apps.program.copying import copy_sessions
from apps.program.models import TimeSlot, Track

from .models import (
    Event,
    EventContract,
    EventRoom,
    EventSeries,
    EventStaffAssignment,
    EventVenue,
    Registration,
    RegistrationSnapshot,
    RoomPickupSnapshot,
)


class EventVenueInline(OrgScopedInline, admin.TabularInline):
    model = EventVenue
    extra = 0
    autocomplete_fields = ["venue"]


class EventRoomInline(OrgScopedInline, admin.TabularInline):
    model = EventRoom
    extra = 0
    autocomplete_fields = ["room"]
    fields = [
        "room", "display_name", "sort_order", "setup", "capacity", "computer_os",
        "is_recorded", "is_schedulable", "is_public",
    ]  # fmt: skip


class TrackInline(OrgScopedInline, admin.TabularInline):
    model = Track
    extra = 0
    prepopulated_fields = {"slug": ["name"]}
    fields = ["name", "slug", "color", "sort_order"]


class TimeSlotInline(OrgScopedInline, admin.TabularInline):
    model = TimeSlot
    extra = 0
    fields = ["starts_at", "ends_at", "label", "kind"]


class StaffInline(OrgScopedInline, admin.TabularInline):
    model = EventStaffAssignment
    extra = 0


@admin.register(EventSeries)
class EventSeriesAdmin(OrgScopedAdmin):
    list_display = ["name", "organization", "visibility"]
    list_filter = ["organization", "visibility"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ["name"]}


@admin.register(Event)
class EventAdmin(EventTimezoneMixin, OrgScopedAdmin):
    """The event hub. Time slot times are entered in the event's timezone."""

    event_path = ""
    list_display = ["name", "organization", "start_date", "end_date", "status", "visibility", "publish_schedule"]
    list_filter = ["organization", "status", "kind", "format", "visibility", "series"]
    search_fields = ["name", "short_name", "slug"]
    prepopulated_fields = {"slug": ["name"]}
    date_hierarchy = "start_date"
    inlines = [EventVenueInline, EventRoomInline, TrackInline, TimeSlotInline, StaffInline]
    readonly_fields = ["schedule_summary"]
    actions = ["report_conflicts", "copy_stock_from_previous", "publish_selected", "sync_registrations"]
    fieldsets = (
        (None, {"fields": ("organization", "series", "name", "slug", "short_name", "kind", "format", "status")}),
        ("When", {"fields": ("start_date", "end_date", "timezone")}),
        ("Description", {"fields": ("description", "intro_text", "banner_image", "expected_attendance")}),
        (
            "Sharing & publishing",
            {
                "fields": (
                    "visibility",
                    "publish_schedule",
                    "publish_speakers",
                    "publish_sponsors",
                    "publish_exhibitors",
                    "publish_logistics",
                )
            },
        ),
        ("Pitches", {"fields": ("pitch_window_opens_at", "pitch_window_closes_at", "pitch_instructions")}),
        (
            "Registration",
            {"fields": ("registration_url", "details_url", "registration_connector", "additional_links")},
        ),
        ("Schedule status", {"fields": ("schedule_summary",)}),
    )

    @admin.display(description="Schedule")
    def schedule_summary(self, obj):
        if not obj.pk:
            return "—"
        sessions = obj.sessions.active()
        conflicts = event_conflicts(obj)
        errors = [c for c in conflicts if c.severity == Severity.ERROR]
        summary = format_html(
            "{} active sessions · {} ready to publish · {} unscheduled · <strong>{}</strong> conflicts, {} warnings",
            sessions.count(),
            sessions.ready().count(),
            sessions.filter(starts_at__isnull=True).count(),
            len(errors),
            len(conflicts) - len(errors),
        )
        if not conflicts:
            return summary
        items = format_html_join("", "<li>[{}] {}</li>", ((c.severity, c.message) for c in conflicts[:50]))
        return format_html("{}<ul>{}</ul>", summary, items)

    @admin.action(description="Report schedule conflicts")
    def report_conflicts(self, request, queryset):
        for event in queryset:
            conflicts = event_conflicts(event)
            if not conflicts:
                self.message_user(request, f"{event}: no conflicts.", messages.SUCCESS)
            for c in conflicts:
                level = messages.ERROR if c.severity == Severity.ERROR else messages.WARNING
                self.message_user(request, f"{event}: {c.message}", level)

    @admin.action(description="Copy stock sessions from the previous event in the series")
    def copy_stock_from_previous(self, request, queryset):
        for event in queryset:
            if not event.series_id:
                self.message_user(request, f"{event}: not part of a series.", messages.WARNING)
                continue
            previous = (
                Event.objects.filter(series_id=event.series_id, start_date__lt=event.start_date)
                .order_by("-start_date")
                .first()
            )
            if previous is None:
                self.message_user(request, f"{event}: no earlier event in the series.", messages.WARNING)
                continue
            result = copy_sessions(previous, event)
            self.message_user(
                request,
                f"{event}: copied {len(result.created)} stock sessions from {previous} "
                f"({len(result.skipped)} skipped, {len(result.tracks_created)} tracks created).",
                messages.SUCCESS,
            )


class PickupInline(OrgScopedInline, admin.TabularInline):
    model = RoomPickupSnapshot
    extra = 1


@admin.register(EventContract)
class EventContractAdmin(OrgScopedAdmin):
    """Contract terms — private to the organization, never shared or exported."""

    list_display = ["__str__", "event", "venue", "attendee_room_rate", "room_block_total", "latest_pickup_display"]
    list_filter = ["event__organization", "event"]
    autocomplete_fields = ["event", "venue"]
    inlines = [PickupInline]
    fieldsets = (
        (None, {"fields": ("event", "venue", "title", "contract_file", "signed_on")}),
        (
            "Food & beverage minimums",
            {
                "fields": (
                    "fnb_minimum_inclusive",
                    "fnb_minimum_exclusive",
                    "affiliate_fnb_minimum_inclusive",
                    "affiliate_fnb_minimum_exclusive",
                )
            },
        ),
        ("Other costs", {"fields": ("av_cost", "attendee_room_rate")}),
        ("Room block", {"fields": ("room_block_total", "room_block_cutoff", "pickup_tracking_url")}),
        ("Notes", {"fields": ("notes",)}),
    )

    @admin.display(description="Latest pickup")
    def latest_pickup_display(self, obj):
        snap = obj.latest_pickup
        if not snap:
            return "—"
        if obj.room_block_total:
            return f"{snap.rooms_picked_up} / {obj.room_block_total} ({snap.recorded_on})"
        return f"{snap.rooms_picked_up} ({snap.recorded_on})"


@admin.register(Registration)
class RegistrationAdmin(OrgScopedAdmin):
    list_display = ["__str__", "event", "ticket_type", "registered_at", "canceled_at", "source"]
    list_filter = ["event__organization", "event", "source"]
    search_fields = ["first_name", "last_name", "email", "affiliation"]
    date_hierarchy = "registered_at"
    autocomplete_fields = ["event", "speaker"]


@admin.register(RegistrationSnapshot)
class RegistrationSnapshotAdmin(OrgScopedAdmin):
    list_display = ["event", "recorded_on", "total_registrations", "source"]
    list_filter = ["event__organization", "event"]

    @admin.action(description="Publish schedule feeds (JSON, CSV, TXT, ICS, PDF, schedule UI)")
    def publish_selected(self, request, queryset):
        from apps.publishing.tasks import publish_event_task

        for event in queryset:
            if not event.publish_schedule:
                self.message_user(request, f"{event}: turn on 'publish schedule' first.", messages.WARNING)
                continue
            publish_event_task.enqueue(event.pk, None, request.user.pk)
            self.message_user(request, f"{event}: publish queued — see Publish runs.", messages.SUCCESS)

    @admin.action(description="Sync registrations from the registration connector")
    def sync_registrations(self, request, queryset):
        from apps.integrations.tasks import sync_event_registrations

        for event in queryset:
            sync_event_registrations.enqueue(event.pk, request.user.pk)
            self.message_user(request, f"{event}: registration sync queued — see Sync runs.", messages.SUCCESS)
