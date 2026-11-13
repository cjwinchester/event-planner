from django.contrib import admin, messages
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from apps.core.admin import EventTimezoneMixin, OrgScopedAdmin, OrgScopedInline

from .conflicts import Severity, check_logistics_item, check_session
from .models import (
    LogisticsCategory,
    LogisticsItem,
    Session,
    SessionAsset,
    SessionSpeaker,
    SessionType,
    SkillLevel,
    TimeSlot,
    Track,
)


class _EventRoomLimitMixin:
    """On a change form, only offer rooms (and tracks) from the object's event."""

    def get_form(self, request, obj=None, **kwargs):
        request._admin_event_id = getattr(obj, "event_id", None) or request.GET.get("event")
        return super().get_form(request, obj, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        event_id = getattr(request, "_admin_event_id", None)
        if db_field.name == "room" and event_id:
            from apps.events.models import EventRoom

            kwargs["queryset"] = EventRoom.objects.filter(event_id=event_id).select_related("room", "event")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        event_id = getattr(request, "_admin_event_id", None)
        if db_field.name == "tracks" and event_id:
            kwargs["queryset"] = Track.objects.filter(event_id=event_id)
        return super().formfield_for_manytomany(db_field, request, **kwargs)


def _render_conflicts(conflicts):
    if not conflicts:
        return mark_safe('<span style="color:green">No conflicts</span>')
    return format_html(
        "<ul>{}</ul>",
        format_html_join(
            "",
            '<li style="color:{}">{}</li>',
            (("#b00" if c.severity == Severity.ERROR else "#a60", c.message) for c in conflicts),
        ),
    )


class SessionSpeakerInline(OrgScopedInline, admin.TabularInline):
    model = SessionSpeaker
    extra = 0
    autocomplete_fields = ["speaker"]
    fields = ["speaker", "role", "sort_order", "affiliation_override", "is_confirmed"]


class SessionAssetInline(OrgScopedInline, admin.TabularInline):
    model = SessionAsset
    extra = 0
    fields = ["kind", "title", "file", "url", "speaker", "is_public", "sort_order"]
    autocomplete_fields = ["speaker"]


@admin.register(Session)
class SessionAdmin(_EventRoomLimitMixin, EventTimezoneMixin, OrgScopedAdmin):
    """Sessions. Saving runs room/speaker conflict checks (errors block the
    save); warnings show in the "Conflicts" panel. Times are in the event's
    timezone."""

    list_display = [
        "public_id", "title", "event", "room", "local_start", "status", "title_status", "ready",
    ]  # fmt: skip
    list_display_links = ["public_id", "title"]
    list_filter = [
        "event__organization", "event", "status", "title_status", "description_status", "source",
        "is_stock", "is_plenary", "session_type", "tracks",
    ]  # fmt: skip
    search_fields = [
        "title", "description", "session_speakers__speaker__first_name", "session_speakers__speaker__last_name",
    ]  # fmt: skip
    autocomplete_fields = ["event"]
    filter_horizontal = ["tracks"]
    readonly_fields = ["public_id", "conflict_report", "readiness"]
    inlines = [SessionSpeakerInline, SessionAssetInline]
    actions = ["mark_title_edited", "mark_stock", "cancel_sessions"]
    fieldsets = (
        (None, {"fields": ("event", "public_id", "title", "title_status", "summary")}),
        ("Description", {"fields": ("description", "description_status")}),
        ("Classification", {"fields": ("session_type", "skill_level", "tracks", "status", "source", "owner")}),
        ("Placement", {"fields": ("room", "starts_at", "ends_at", "is_plenary", "conflict_report", "readiness")}),
        (
            "Logistics",
            {
                "classes": ("collapse",),
                "fields": (
                    "setup",
                    "expected_headcount",
                    "capacity",
                    "requires_preregistration",
                    "cost",
                    "av_requirements",
                    "fnb_requirements",
                    "hotel_notes",
                ),
            },
        ),
        ("Media", {"fields": ("is_recorded", "live_url", "recording_url")}),
        ("Internal", {"fields": ("is_stock", "copied_from", "working_folder_url", "internal_notes", "extra")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("event", "room__room", "room__event")

    @admin.display(description="Starts (event time)", ordering="starts_at")
    def local_start(self, obj):
        return obj.starts_at.astimezone(obj.event.tz).strftime("%a %b %-d %-I:%M %p") if obj.starts_at else "—"

    @admin.display(boolean=True)
    def ready(self, obj):
        return obj.is_ready

    @admin.display(description="Conflicts")
    def conflict_report(self, obj):
        return _render_conflicts(check_session(obj)) if obj.pk else "—"

    @admin.display(description="Ready to publish?")
    def readiness(self, obj):
        if not obj.pk:
            return "—"
        if obj.is_ready:
            return mark_safe('<span style="color:green">Ready</span>')
        return "Not yet: " + ", ".join(obj.readiness_problems)

    @admin.action(description="Mark title (and description) as edited")
    def mark_title_edited(self, request, queryset):
        n = queryset.update(title_status=Session.EditStatus.EDITED, description_status=Session.EditStatus.EDITED)
        self.message_user(request, f"Marked {n} sessions edited.", messages.SUCCESS)

    @admin.action(description="Flag as stock session (offered when copying events)")
    def mark_stock(self, request, queryset):
        self.message_user(request, f"Flagged {queryset.update(is_stock=True)} sessions as stock.", messages.SUCCESS)

    @admin.action(description="Cancel selected sessions")
    def cancel_sessions(self, request, queryset):
        n = queryset.update(status=Session.Status.CANCELED)
        self.message_user(request, f"Canceled {n} sessions.", messages.SUCCESS)


@admin.register(LogisticsItem)
class LogisticsItemAdmin(_EventRoomLimitMixin, EventTimezoneMixin, OrgScopedAdmin):
    list_display = ["title", "category", "event", "room", "location_note", "starts_at", "public"]
    list_filter = ["event__organization", "event", "category"]
    search_fields = ["title", "description", "location_note"]
    autocomplete_fields = ["event"]
    readonly_fields = ["conflict_report"]
    fieldsets = (
        (None, {"fields": ("event", "category", "title", "description", "owner")}),
        ("Placement", {"fields": ("room", "location_note", "starts_at", "ends_at", "blocks_room", "conflict_report")}),
        ("Attendees", {"fields": ("is_public",)}),
        (
            "Hotel spec sheet",
            {"fields": ("setup", "expected_headcount", "av_requirements", "fnb_requirements", "hotel_notes")},
        ),
    )

    @admin.display(boolean=True, description="Public")
    def public(self, obj):
        return obj.effective_is_public

    @admin.display(description="Conflicts")
    def conflict_report(self, obj):
        return _render_conflicts(check_logistics_item(obj)) if obj.pk else "—"


@admin.register(Track)
class TrackAdmin(OrgScopedAdmin):
    list_display = ["name", "event", "sort_order"]
    list_filter = ["event__organization", "event"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ["name"]}


@admin.register(TimeSlot)
class TimeSlotAdmin(EventTimezoneMixin, OrgScopedAdmin):
    list_display = ["__str__", "event", "kind"]
    list_filter = ["event__organization", "event", "kind"]


@admin.register(SessionType)
class SessionTypeAdmin(OrgScopedAdmin):
    list_display = ["name", "organization", "is_hands_on", "default_duration_minutes", "is_active"]
    list_filter = ["organization"]


@admin.register(SkillLevel)
class SkillLevelAdmin(OrgScopedAdmin):
    list_display = ["name", "organization", "sort_order"]
    list_filter = ["organization"]


@admin.register(LogisticsCategory)
class LogisticsCategoryAdmin(OrgScopedAdmin):
    list_display = ["name", "organization", "blocks_room", "public_by_default"]
    list_filter = ["organization"]
