"""API serializers. Org-owned models use OrgModelSerializer (scoped related
fields + model clean()); shared-directory models use plain serializers.

Speaker private fields (email, phone, location) are only included when the
requesting user's organization holds an active share grant, or for the
speaker themself (see speakers.sharing)."""

from __future__ import annotations

from rest_framework import serializers

from apps.commerce.models import Company, ExhibitorBooking, Sponsorship
from apps.events.models import Event, EventRoom, EventSeries, EventVenue
from apps.organizations.models import Organization
from apps.organizations.permissions import organization_ids_for
from apps.pitches.models import Pitch, PitchTarget, PitchVote
from apps.program.models import LogisticsItem, Session, SessionSpeaker, TimeSlot, Track
from apps.publishing.models import PublishRun
from apps.speakers.models import SpeakerLink, SpeakerProfile, SpeakerShareGrant
from apps.venues.models import Floorplan, Room, RoomCapacity, Venue

from .base import OrgModelSerializer


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "slug", "website", "description", "default_timezone"]


# ------------------------------------------------------------------ venues


class RoomCapacitySerializer(serializers.ModelSerializer):
    class Meta:
        model = RoomCapacity
        fields = ["setup", "capacity"]


class RoomSerializer(serializers.ModelSerializer):
    capacities = RoomCapacitySerializer(many=True, read_only=True)

    class Meta:
        model = Room
        fields = [
            "id", "venue", "name", "parts", "floorplan", "floor_label", "square_feet", "map_x", "map_y",
            "map_shape", "accessibility_notes", "join_url", "is_active", "sort_order", "capacities",
        ]  # fmt: skip


class FloorplanSerializer(serializers.ModelSerializer):
    leaflet_bounds = serializers.ListField(read_only=True)

    class Meta:
        model = Floorplan
        fields = ["id", "venue", "label", "level", "image", "width", "height", "caption", "leaflet_bounds"]
        read_only_fields = ["width", "height"]


class VenueSerializer(serializers.ModelSerializer):
    lat = serializers.SerializerMethodField()
    lon = serializers.SerializerMethodField()

    class Meta:
        model = Venue
        fields = [
            "id", "name", "kind", "address1", "address2", "city", "region", "postal_code", "country", "lat",
            "lon", "timezone", "website", "phone", "email", "maps_url", "public_access_notes",
        ]  # fmt: skip

    def get_lat(self, obj) -> float | None:
        return obj.location.y if obj.location else None

    def get_lon(self, obj) -> float | None:
        return obj.location.x if obj.location else None


# ------------------------------------------------------------------ events


class EventSeriesSerializer(OrgModelSerializer):
    class Meta:
        model = EventSeries
        fields = ["id", "organization", "name", "slug", "description", "visibility"]


class EventVenueSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventVenue
        fields = ["venue", "role", "sort_order"]


class EventSerializer(OrgModelSerializer):
    event_venues = EventVenueSerializer(many=True, read_only=True)
    is_pitch_window_open = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            "id", "organization", "series", "name", "slug", "short_name", "kind", "format", "status",
            "description", "intro_text", "start_date", "end_date", "timezone", "visibility",
            "publish_schedule", "publish_speakers", "publish_sponsors", "publish_exhibitors",
            "publish_logistics", "pitch_window_opens_at", "pitch_window_closes_at", "pitch_instructions",
            "is_pitch_window_open", "registration_url", "details_url", "registration_connector",
            "additional_links", "expected_attendance", "event_venues",
        ]  # fmt: skip

    def get_is_pitch_window_open(self, obj) -> bool:
        return obj.is_pitch_window_open()


class CalendarEventSerializer(serializers.ModelSerializer):
    """Cross-organization calendar entry: no private fields."""

    organization = serializers.CharField(source="organization.name")
    city = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = ["id", "organization", "name", "kind", "format", "start_date", "end_date", "city", "details_url"]

    def get_city(self, obj) -> str:
        venue = obj.primary_venue
        return f"{venue.city}, {venue.region}".strip(", ") if venue else ""


class EventRoomSerializer(OrgModelSerializer):
    name = serializers.CharField(read_only=True)
    effective_capacity = serializers.IntegerField(read_only=True)

    class Meta:
        model = EventRoom
        fields = [
            "id", "event", "room", "name", "display_name", "sort_order", "setup", "capacity",
            "effective_capacity", "computer_os", "is_recorded", "is_schedulable", "is_public", "notes",
        ]  # fmt: skip


# ------------------------------------------------------------------ program


class TrackSerializer(OrgModelSerializer):
    class Meta:
        model = Track
        fields = ["id", "event", "name", "slug", "description", "color", "sort_order"]


class TimeSlotSerializer(OrgModelSerializer):
    class Meta:
        model = TimeSlot
        fields = ["id", "event", "starts_at", "ends_at", "label", "kind"]


class SessionSpeakerSerializer(OrgModelSerializer):
    speaker_name = serializers.CharField(source="speaker.full_name", read_only=True)
    speaker_public_id = serializers.CharField(source="speaker.public_id", read_only=True)

    class Meta:
        model = SessionSpeaker
        fields = [
            "id", "session", "speaker", "speaker_name", "speaker_public_id", "role", "sort_order",
            "affiliation_override", "is_confirmed",
        ]  # fmt: skip


class SessionSerializer(OrgModelSerializer):
    """Create/edit sessions. Room and times are read-only here: move sessions
    with POST /sessions/{id}/place/ so conflict checks and locking apply."""

    speakers = SessionSpeakerSerializer(source="session_speakers", many=True, read_only=True)
    is_ready = serializers.BooleanField(read_only=True)
    readiness_problems = serializers.ListField(read_only=True)

    class Meta:
        model = Session
        fields = [
            "id", "event", "public_id", "title", "title_status", "description", "description_status", "summary",
            "session_type", "skill_level", "tracks", "status", "source", "owner", "is_plenary", "is_stock",
            "requires_preregistration", "capacity", "cost", "is_recorded", "live_url", "recording_url",
            "working_folder_url", "internal_notes", "extra", "setup", "expected_headcount", "av_requirements",
            "fnb_requirements", "hotel_notes", "room", "starts_at", "ends_at", "is_ready", "readiness_problems",
            "speakers", "updated_at",
        ]  # fmt: skip
        read_only_fields = ["public_id", "room", "starts_at", "ends_at", "updated_at"]


class LogisticsItemSerializer(OrgModelSerializer):
    class Meta:
        model = LogisticsItem
        fields = [
            "id", "event", "category", "title", "description", "location_note", "blocks_room", "is_public",
            "owner", "setup", "expected_headcount", "av_requirements", "fnb_requirements", "hotel_notes",
            "room", "starts_at", "ends_at",
        ]  # fmt: skip
        read_only_fields = ["room", "starts_at", "ends_at"]


class PlacementSerializer(serializers.Serializer):
    room = serializers.PrimaryKeyRelatedField(queryset=EventRoom.objects.all(), allow_null=True)
    starts_at = serializers.DateTimeField(allow_null=True)
    ends_at = serializers.DateTimeField(allow_null=True)
    dry_run = serializers.BooleanField(default=False)


class CopySessionsSerializer(serializers.Serializer):
    source_event = serializers.PrimaryKeyRelatedField(queryset=Event.objects.all())
    session_ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    include_speakers = serializers.BooleanField(default=False)
    keep_schedule = serializers.BooleanField(default=False)


# ------------------------------------------------------------------ speakers


class SpeakerLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = SpeakerLink
        fields = ["kind", "url", "label", "sort_order"]


class SpeakerProfileSerializer(serializers.ModelSerializer):
    links = SpeakerLinkSerializer(many=True, required=False)

    class Meta:
        model = SpeakerProfile
        fields = [
            "id", "public_id", "first_name", "last_name", "pronouns", "affiliation", "job_title", "bio",
            "headshot", "headshot_url", "links", *SpeakerProfile.PRIVATE_FIELDS,
        ]  # fmt: skip
        read_only_fields = ["public_id"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self._may_see_private(instance):
            for field in SpeakerProfile.PRIVATE_FIELDS:
                data.pop(field, None)
        return data

    def _may_see_private(self, instance) -> bool:
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        if user.is_superuser or instance.user_id == user.pk:
            return True
        return instance.share_grants.filter(
            organization_id__in=organization_ids_for(user), revoked_at__isnull=True
        ).exists()

    def _save_links(self, speaker, links):
        if links is None:
            return
        speaker.links.all().delete()
        SpeakerLink.objects.bulk_create(SpeakerLink(speaker=speaker, **link) for link in links)

    def create(self, validated_data):
        links = validated_data.pop("links", None)
        speaker = super().create(validated_data)
        self._save_links(speaker, links)
        return speaker

    def update(self, instance, validated_data):
        links = validated_data.pop("links", None)
        speaker = super().update(instance, validated_data)
        self._save_links(speaker, links)
        return speaker


class ShareGrantSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)

    class Meta:
        model = SpeakerShareGrant
        fields = ["organization", "organization_name", "source", "granted_at", "revoked_at"]
        read_only_fields = ["source", "granted_at", "revoked_at"]


# ------------------------------------------------------------------ pitches


class PitchSubmitSerializer(serializers.Serializer):
    """Speaker self-service pitch submission (one pitch → several events)."""

    title = serializers.CharField(max_length=500)
    description = serializers.CharField()
    events = serializers.PrimaryKeyRelatedField(queryset=Event.objects.all(), many=True)
    co_speakers = serializers.SlugRelatedField(
        slug_field="public_id", queryset=SpeakerProfile.objects.all(), many=True, required=False
    )
    format = serializers.CharField(required=False, allow_blank=True)
    skill_level = serializers.CharField(required=False, allow_blank=True)
    speaker_suggestions = serializers.CharField(required=False, allow_blank=True)
    willing_to_organize = serializers.BooleanField(default=True)
    answers = serializers.DictField(
        child=serializers.DictField(), required=False, help_text="{event_id: {slug: answer}}"
    )


class PitchTargetSerializer(OrgModelSerializer):
    title = serializers.CharField(source="pitch.title", read_only=True)
    description = serializers.CharField(source="pitch.description", read_only=True)
    submitted_by = serializers.CharField(source="pitch.submitted_by.public_id", read_only=True)
    format = serializers.CharField(source="pitch.format", read_only=True)
    votes = serializers.SerializerMethodField()

    class Meta:
        model = PitchTarget
        fields = [
            "id", "pitch", "event", "title", "description", "submitted_by", "format", "status", "answers",
            "suggested_tracks", "staff_notes", "assigned_to", "decided_at", "informed_at", "confirmed_at",
            "merged_into", "session", "votes",
        ]  # fmt: skip
        read_only_fields = [
            "pitch", "event", "status", "decided_at", "informed_at", "confirmed_at", "merged_into", "session",
        ]  # fmt: skip

    def get_votes(self, obj) -> list[dict]:
        return [{"user": str(v.user), "vote": v.vote, "comment": v.comment} for v in obj.votes.all()]


class MyPitchSerializer(serializers.ModelSerializer):
    """What a speaker sees about their own pitch: status per event, no staff notes."""

    targets = serializers.SerializerMethodField()

    class Meta:
        model = Pitch
        fields = ["id", "title", "description", "format", "skill_level", "submitted_at", "targets"]

    def get_targets(self, obj) -> list[dict]:
        visible = {PitchTarget.Status.ACCEPTED, PitchTarget.Status.DECLINED, PitchTarget.Status.WITHDRAWN}
        return [
            {
                "event": t.event.name,
                # Queued/waitlisted decisions stay "submitted" until staff inform the pitcher.
                "status": t.status if t.status in visible else PitchTarget.Status.SUBMITTED,
                "informed_at": t.informed_at,
                "confirmed_at": t.confirmed_at,
            }
            for t in obj.targets.select_related("event")
        ]


class DecisionSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField())
    decision = serializers.ChoiceField(
        choices=[PitchTarget.Status.ACCEPT_QUEUE, PitchTarget.Status.DECLINE_QUEUE, PitchTarget.Status.WAITLISTED]
    )


class VoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = PitchVote
        fields = ["vote", "comment"]


# ------------------------------------------------------------------ commerce


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ["id", "name", "slug", "website", "description", "logo", "logo_url"]


class SponsorshipSerializer(OrgModelSerializer):
    ticket_entitlement = serializers.IntegerField(read_only=True)

    class Meta:
        model = Sponsorship
        fields = [
            "id", "event", "company", "level", "scope", "track", "session", "status", "amount", "comp_tickets",
            "ticket_entitlement", "display_name", "is_public", "notes",
        ]  # fmt: skip


class ExhibitorBookingSerializer(OrgModelSerializer):
    ticket_entitlement = serializers.IntegerField(read_only=True)

    class Meta:
        model = ExhibitorBooking
        fields = [
            "id", "event", "company", "package", "status", "booth_number", "booth_room", "amount",
            "comp_tickets", "ticket_entitlement", "is_public", "notes",
        ]  # fmt: skip


class PublishRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = PublishRun
        fields = [
            "id", "event", "status", "target", "formats", "files", "bundle_version", "session_count", "dry_run",
            "started_at", "finished_at", "message",
        ]  # fmt: skip


class PublishRequestSerializer(serializers.Serializer):
    formats = serializers.ListField(child=serializers.CharField(), required=False)
    dry_run = serializers.BooleanField(default=False)


class AskSerializer(serializers.Serializer):
    organization = serializers.PrimaryKeyRelatedField(queryset=Organization.objects.all())
    question = serializers.CharField(max_length=2000)
