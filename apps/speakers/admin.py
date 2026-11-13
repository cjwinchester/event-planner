from django.contrib import admin

from apps.core.admin import OrgScopedAdmin, SharedDirectoryAdmin, SharedDirectoryInline
from apps.organizations.permissions import organization_ids_for

from .models import EventSpeaker, SpeakerLink, SpeakerProfile, SpeakerShareGrant


class SpeakerLinkInline(SharedDirectoryInline, admin.TabularInline):
    model = SpeakerLink
    extra = 0


@admin.register(SpeakerProfile)
class SpeakerProfileAdmin(SharedDirectoryAdmin):
    """Shared speaker directory. Private fields (email, phone, location) only
    appear for organizations the speaker has shared them with."""

    list_display = ["full_name", "affiliation", "public_id", "claimed", "is_active"]
    search_fields = ["first_name", "last_name", "affiliation", "email"]
    list_filter = ["is_active"]
    inlines = [SpeakerLinkInline]
    readonly_fields = ["public_id", "user", "merged_into", "created_by_organization"]

    @admin.display(boolean=True, description="Claimed")
    def claimed(self, obj):
        return obj.user_id is not None

    def _can_see_private(self, request, obj) -> bool:
        if request.user.is_superuser or obj is None:
            return True  # new profiles: the creating org enters contact info
        return obj.share_grants.filter(
            organization_id__in=organization_ids_for(request.user), revoked_at__isnull=True
        ).exists()

    def get_fields(self, request, obj=None):
        fields = [
            "public_id", "first_name", "last_name", "pronouns", "affiliation", "job_title", "bio",
            "headshot", "headshot_url", "is_active", "user", "merged_into", "created_by_organization",
        ]  # fmt: skip
        if self._can_see_private(request, obj):
            fields[5:5] = list(SpeakerProfile.PRIVATE_FIELDS)
        return fields

    def get_search_fields(self, request):
        # Don't let orgs discover private emails through search.
        return self.search_fields if request.user.is_superuser else ["first_name", "last_name", "affiliation"]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change and obj.created_by_organization_id:
            from . import sharing

            sharing.grant(obj, obj.created_by_organization, SpeakerShareGrant.Source.CREATED)


@admin.register(EventSpeaker)
class EventSpeakerAdmin(OrgScopedAdmin):
    list_display = ["speaker", "event", "status", "comp_status", "registration_status", "is_featured"]
    list_filter = ["event__organization", "event", "status", "comp_status", "is_featured"]
    search_fields = ["speaker__first_name", "speaker__last_name", "speaker__affiliation"]
    autocomplete_fields = ["event", "speaker"]


@admin.register(SpeakerShareGrant)
class SpeakerShareGrantAdmin(OrgScopedAdmin):
    """Read-only view of which speakers share private details with your org.
    Speakers manage grants themselves (portal/API)."""

    list_display = ["speaker", "organization", "source", "granted_at", "revoked_at"]
    list_filter = ["organization", "source"]
    readonly_fields = ["speaker", "organization", "source", "granted_at", "revoked_at", "revoked_by_speaker"]

    def has_add_permission(self, request, *args):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser
