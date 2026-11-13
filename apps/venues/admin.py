from django.contrib import admin, messages
from django.contrib.gis.admin import GISModelAdmin
from django.utils.html import format_html_join

from apps.core.admin import OrgScopedAdmin, SharedDirectoryAdmin, SharedDirectoryInline
from apps.core.models import SHARED_VISIBILITIES
from apps.organizations.permissions import organization_ids_for

from .geocoding import geocode_and_save
from .models import Floorplan, Room, RoomCapacity, Venue, VenueContact, VenueNote


class FloorplanInline(SharedDirectoryInline, admin.TabularInline):
    model = Floorplan
    extra = 0
    fields = ["label", "level", "image", "caption"]


class RoomInline(SharedDirectoryInline, admin.TabularInline):
    model = Room
    extra = 0
    fields = ["name", "floorplan", "floor_label", "square_feet", "join_url", "is_active", "sort_order"]
    show_change_link = True


class RoomCapacityInline(SharedDirectoryInline, admin.TabularInline):
    model = RoomCapacity
    extra = 0


@admin.register(Venue)
class VenueAdmin(SharedDirectoryAdmin, GISModelAdmin):
    list_display = ["name", "kind", "city", "region", "country", "has_location"]
    list_filter = ["kind", "country", "region"]
    search_fields = ["name", "city", "address1"]
    inlines = [FloorplanInline, RoomInline]
    readonly_fields = ["geocode_source", "shared_insights", "created_by_organization"]
    actions = ["geocode_selected"]
    fieldsets = (
        (None, {"fields": ("name", "kind", "website", "phone", "email", "maps_url")}),
        ("Address", {"fields": ("address1", "address2", "city", "region", "postal_code", "country", "timezone")}),
        ("Map", {"fields": ("location", "geocode_source")}),
        ("Attendees", {"fields": ("public_access_notes",)}),
        ("Notes from organizations", {"fields": ("shared_insights",)}),
        ("Provenance", {"fields": ("created_by_organization",)}),
    )

    @admin.display(boolean=True, description="Geocoded")
    def has_location(self, obj):
        return obj.location is not None

    @admin.display(description="Notes visible to you")
    def shared_insights(self, obj):
        if not obj.pk:
            return "—"
        viewer = getattr(obj, "_admin_viewer", None)
        notes = obj.notes.select_related("organization")
        if viewer is None or not viewer.is_superuser:
            mine = organization_ids_for(viewer) if viewer else []
            notes = [n for n in notes if n.organization_id in mine or n.visibility in SHARED_VISIBILITIES]
        if not notes:
            return "None yet."
        return format_html_join(
            "",
            "<p><strong>{}</strong> ({}): {}</p>",
            ((n.organization, n.get_visibility_display(), n.body) for n in notes),
        )

    def get_object(self, request, object_id, from_field=None):
        # Per-request viewer for shared_insights (ModelAdmin is shared across
        # threads, so never stash request state on `self`).
        obj = super().get_object(request, object_id, from_field)
        if obj is not None:
            obj._admin_viewer = request.user
        return obj

    @admin.action(description="Geocode selected venues")
    def geocode_selected(self, request, queryset):
        ok = sum(geocode_and_save(v) for v in queryset)
        level = messages.SUCCESS if ok == queryset.count() else messages.WARNING
        self.message_user(request, f"Geocoded {ok} of {queryset.count()} venues.", level)


@admin.register(Room)
class RoomAdmin(SharedDirectoryAdmin):
    list_display = ["name", "venue", "floor", "square_feet", "is_combination"]
    list_filter = ["venue"]
    search_fields = ["name", "venue__name"]
    filter_horizontal = ["parts"]
    inlines = [RoomCapacityInline]

    @admin.display(boolean=True, description="Combined")
    def is_combination(self, obj):
        return obj.parts.exists()


@admin.register(Floorplan)
class FloorplanAdmin(SharedDirectoryAdmin, GISModelAdmin):
    list_display = ["__str__", "venue", "level", "width", "height"]
    list_filter = ["venue"]


@admin.register(VenueContact)
class VenueContactAdmin(OrgScopedAdmin):
    list_display = ["name", "role", "venue", "organization", "visibility"]
    list_filter = ["organization", "visibility"]
    search_fields = ["name", "venue__name"]
    autocomplete_fields = ["venue"]


@admin.register(VenueNote)
class VenueNoteAdmin(OrgScopedAdmin):
    list_display = ["__str__", "venue", "organization", "rating", "visibility", "created_at"]
    list_filter = ["organization", "visibility"]
    search_fields = ["title", "body", "venue__name"]
    autocomplete_fields = ["venue", "event"]

    def save_model(self, request, obj, form, change):
        if not change and not obj.author_id:
            obj.author = request.user
        super().save_model(request, obj, form, change)
