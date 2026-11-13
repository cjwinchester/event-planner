"""REST API v1 viewsets. See docs/api.md for the endpoint map and the
OpenAPI UI at /api/docs/."""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.assistant.llm import LLMNotConfigured, QueryService
from apps.commerce.models import Company, ExhibitorBooking, Sponsorship
from apps.core.models import SHARED_VISIBILITIES
from apps.events.models import Event, EventRoom, EventSeries
from apps.organizations.models import Organization
from apps.organizations.permissions import has_role, organization_ids_for
from apps.pitches import services as pitch_services
from apps.pitches.models import Pitch, PitchTarget, PitchVote
from apps.program.conflicts import event_conflicts
from apps.program.copying import copy_sessions
from apps.program.models import LogisticsItem, Session, SessionSpeaker, TimeSlot, Track
from apps.program.scheduling import PlacementError, place_logistics_item, place_session
from apps.publishing.bundle import Audience, build_event_bundle
from apps.publishing.models import PublishRun
from apps.reporting.registry import ReportScope, all_reports
from apps.speakers import sharing
from apps.speakers.models import SpeakerProfile, SpeakerShareGrant
from apps.venues.models import Floorplan, Room, Venue

from . import serializers as s
from .base import OrgScopedViewSet, Role, SharedDirectoryPermission

# ------------------------------------------------------------------ orgs & calendar


class OrganizationViewSet(viewsets.ReadOnlyModelViewSet):
    """Organizations the user belongs to."""

    serializer_class = s.OrganizationSerializer

    def get_queryset(self):
        return Organization.objects.filter(pk__in=organization_ids_for(self.request.user))


class CalendarViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """The shared cross-organization calendar of upcoming events (NETWORK/PUBLIC)."""

    serializer_class = s.CalendarEventSerializer

    def get_queryset(self):
        return (
            Event.objects.upcoming()
            .filter(visibility__in=SHARED_VISIBILITIES)
            .select_related("organization")
            .order_by("start_date")
        )


# ------------------------------------------------------------------ venues (shared)


class VenueViewSet(viewsets.ModelViewSet):
    queryset = Venue.objects.all()
    serializer_class = s.VenueSerializer
    permission_classes = [SharedDirectoryPermission]
    search_fields = ["name", "city"]
    filterset_fields = ["kind", "city", "region", "country"]


class RoomViewSet(viewsets.ModelViewSet):
    queryset = Room.objects.prefetch_related("capacities", "parts")
    serializer_class = s.RoomSerializer
    permission_classes = [SharedDirectoryPermission]
    filterset_fields = ["venue"]


class FloorplanViewSet(viewsets.ModelViewSet):
    queryset = Floorplan.objects.all()
    serializer_class = s.FloorplanSerializer
    permission_classes = [SharedDirectoryPermission]
    filterset_fields = ["venue"]


# ------------------------------------------------------------------ events


class EventSeriesViewSet(OrgScopedViewSet):
    queryset = EventSeries.objects.all()
    serializer_class = s.EventSeriesSerializer
    filterset_fields = ["organization"]


class EventViewSet(OrgScopedViewSet):
    queryset = Event.objects.select_related("organization", "series").prefetch_related("event_venues")
    serializer_class = s.EventSerializer
    filterset_fields = ["organization", "series", "status", "kind"]
    search_fields = ["name", "short_name"]

    @action(detail=True)
    def bundle(self, request, pk=None):
        """The Event Bundle. ?audience=internal (staff) includes unscheduled sessions."""
        event = self.get_object()
        audience = Audience.INTERNAL if request.query_params.get("audience") == "internal" else Audience.PUBLIC
        return Response(build_event_bundle(event, audience))

    @action(detail=True)
    def conflicts(self, request, pk=None):
        return Response([c.to_dict() for c in event_conflicts(self.get_object())])

    @action(detail=True)
    def grid(self, request, pk=None):
        """Everything the drag-and-drop planner needs in one call."""
        event = self.get_object()
        rooms = event.event_rooms.filter(is_schedulable=True).select_related("room")
        sessions = (
            Session.objects.active()
            .filter(event=event)
            .select_related("session_type")
            .prefetch_related("tracks", "session_speakers__speaker")
        )
        logistics = LogisticsItem.objects.filter(event=event).select_related("category")

        def session_item(x):
            return {
                "type": "session",
                "id": x.pk,
                "public_id": x.public_id,
                "title": x.title,
                "room": x.room_id,
                "starts_at": x.starts_at,
                "ends_at": x.ends_at,
                "status": x.status,
                "is_ready": x.is_ready,
                "is_plenary": x.is_plenary,
                "session_type": x.session_type.name if x.session_type else "",
                "tracks": [t.slug for t in x.tracks.all()],
                "speakers": [ss.speaker.full_name for ss in x.session_speakers.all()],
            }

        def logistics_item(x):
            return {
                "type": "logistics",
                "id": x.pk,
                "title": x.title,
                "category": x.category.name,
                "room": x.room_id,
                "location_note": x.location_note,
                "starts_at": x.starts_at,
                "ends_at": x.ends_at,
                "blocks_room": x.effective_blocks_room,
            }

        placed = [session_item(x) for x in sessions if x.is_placed] + [
            logistics_item(x) for x in logistics if x.starts_at
        ]
        return Response(
            {
                "event": {"id": event.pk, "name": event.name, "timezone": event.timezone, "days": event.days},
                "rooms": [
                    {"id": r.pk, "name": r.name, "capacity": r.effective_capacity, "sort_order": r.sort_order}
                    for r in rooms
                ],
                "time_slots": s.TimeSlotSerializer(event.time_slots.all(), many=True).data,
                "items": placed,
                "unscheduled": [session_item(x) for x in sessions if not x.is_placed]
                + [logistics_item(x) for x in logistics if not x.starts_at],
                "conflicts": [c.to_dict() for c in event_conflicts(event)],
            }
        )

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        """Queue a publish run (background task). Returns 202."""
        from apps.publishing.tasks import publish_event_task

        event = self.get_object()
        self._check_write(event)
        body = s.PublishRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        if not event.publish_schedule:
            raise ValidationError("Turn on publish_schedule first.")
        result = publish_event_task.enqueue(event.pk, body.validated_data.get("formats"), request.user.pk)
        return Response({"task_id": str(result.id), "status": str(result.status)}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["post"], url_path="copy-sessions")
    def copy_sessions(self, request, pk=None):
        target = self.get_object()
        self._check_write(target)
        body = s.CopySessionsSerializer(data=request.data, context={"request": request})
        body.is_valid(raise_exception=True)
        source = body.validated_data["source_event"]
        if not has_role(request.user, source.organization, Role.VIEWER):
            raise PermissionDenied("You can't read the source event.")
        ids = body.validated_data.get("session_ids")
        sessions = source.sessions.filter(pk__in=ids) if ids else None
        try:
            result = copy_sessions(
                source,
                target,
                sessions,
                include_speakers=body.validated_data["include_speakers"],
                keep_schedule=body.validated_data["keep_schedule"],
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return Response(
            {
                "created": [x.pk for x in result.created],
                "skipped": [{"id": x.pk, "reason": why} for x, why in result.skipped],
                "tracks_created": [t.slug for t in result.tracks_created],
            },
            status=status.HTTP_201_CREATED,
        )


class EventRoomViewSet(OrgScopedViewSet):
    queryset = EventRoom.objects.select_related("room", "event")
    serializer_class = s.EventRoomSerializer
    filterset_fields = ["event"]


# ------------------------------------------------------------------ program


class TrackViewSet(OrgScopedViewSet):
    queryset = Track.objects.all()
    serializer_class = s.TrackSerializer
    filterset_fields = ["event"]


class TimeSlotViewSet(OrgScopedViewSet):
    queryset = TimeSlot.objects.all()
    serializer_class = s.TimeSlotSerializer
    filterset_fields = ["event"]


class _PlaceMixin:
    place_fn = None

    @action(detail=True, methods=["post"])
    def place(self, request, pk=None):
        """Move to a room/time. 200 when placed (or dry-run OK), 409 with the
        conflicts when blocked. Body: {room, starts_at, ends_at, dry_run}."""
        obj = self.get_object()
        self._check_write(obj)
        body = s.PlacementSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        d = body.validated_data
        try:
            result = type(self).place_fn(
                obj, room=d["room"], starts_at=d["starts_at"], ends_at=d["ends_at"], dry_run=d["dry_run"]
            )
        except PlacementError as exc:
            raise ValidationError(str(exc)) from exc
        return Response(result.to_dict(), status=status.HTTP_200_OK if result.ok else status.HTTP_409_CONFLICT)


class SessionViewSet(_PlaceMixin, OrgScopedViewSet):
    queryset = Session.objects.select_related("event", "room__room").prefetch_related(
        "tracks", "session_speakers__speaker"
    )
    serializer_class = s.SessionSerializer
    filterset_fields = ["event", "status", "title_status", "is_stock", "session_type", "room"]
    search_fields = ["title", "description"]
    place_fn = staticmethod(place_session)

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("ready") in ("1", "true"):
            qs = qs.ready()
        return qs


class SessionSpeakerViewSet(OrgScopedViewSet):
    queryset = SessionSpeaker.objects.select_related("speaker", "session")
    serializer_class = s.SessionSpeakerSerializer
    filterset_fields = ["session", "speaker"]


class LogisticsItemViewSet(_PlaceMixin, OrgScopedViewSet):
    queryset = LogisticsItem.objects.select_related("category", "event")
    serializer_class = s.LogisticsItemSerializer
    filterset_fields = ["event", "category"]
    place_fn = staticmethod(place_logistics_item)


# ------------------------------------------------------------------ speakers


class SpeakerViewSet(viewsets.ModelViewSet):
    """Shared speaker directory + speaker self-service under /speakers/me/."""

    queryset = SpeakerProfile.objects.filter(merged_into__isnull=True).prefetch_related("links")
    serializer_class = s.SpeakerProfileSerializer
    permission_classes = [SharedDirectoryPermission]
    search_fields = ["first_name", "last_name", "affiliation"]

    def perform_create(self, serializer):
        orgs = organization_ids_for(self.request.user, Role.STAFF)
        speaker = serializer.save(created_by_organization_id=orgs[0] if orgs else None)
        if speaker.created_by_organization_id:
            sharing.grant(speaker, speaker.created_by_organization, SpeakerShareGrant.Source.CREATED)

    def _me(self, request) -> SpeakerProfile:
        profile = SpeakerProfile.objects.filter(user=request.user).first()
        if profile is None:
            raise NotFound("No speaker profile is linked to your account yet.")
        return profile

    @action(detail=False, methods=["get", "patch"], permission_classes=[permissions.IsAuthenticated])
    def me(self, request):
        """Speaker self-service: read/update your own global profile."""
        if request.method == "GET" and not SpeakerProfile.objects.filter(user=request.user).exists():
            # Claim a staff-created profile by matching the verified login email.
            match = SpeakerProfile.objects.filter(
                email__iexact=request.user.email, user__isnull=True, merged_into__isnull=True
            ).first()
            if match:
                match.user = request.user
                match.save(update_fields=["user", "updated_at"])
        profile = self._me(request)
        if request.method == "PATCH":
            ser = s.SpeakerProfileSerializer(profile, data=request.data, partial=True, context={"request": request})
            ser.is_valid(raise_exception=True)
            ser.save()
            return Response(ser.data)
        return Response(s.SpeakerProfileSerializer(profile, context={"request": request}).data)

    @action(detail=False, methods=["get"], url_path="me/engagements", permission_classes=[permissions.IsAuthenticated])
    def engagements(self, request):
        """Past and upcoming sessions for the logged-in speaker."""
        profile = self._me(request)
        rows = (
            profile.session_roles.select_related("session__event")
            .exclude(session__status=Session.Status.CANCELED)
            .order_by("-session__event__start_date")
        )
        today = timezone.localdate()
        return Response(
            [
                {
                    "event": r.session.event.name,
                    "session": r.session.title,
                    "role": r.role,
                    "starts_at": r.session.starts_at,
                    "upcoming": r.session.event.end_date >= today,
                }
                for r in rows
            ]
        )

    @action(
        detail=False, methods=["get", "post"], url_path="me/grants", permission_classes=[permissions.IsAuthenticated]
    )
    def grants(self, request):
        """List or add organizations allowed to see your contact details."""
        profile = self._me(request)
        if request.method == "POST":
            org = get_object_or_404(Organization, pk=request.data.get("organization"))
            sharing.grant(profile, org, SpeakerShareGrant.Source.SPEAKER)
        return Response(s.ShareGrantSerializer(profile.share_grants.select_related("organization"), many=True).data)

    @action(
        detail=False,
        methods=["delete"],
        url_path=r"me/grants/(?P<org_id>\d+)",
        permission_classes=[permissions.IsAuthenticated],
    )
    def revoke_grant(self, request, org_id=None):
        profile = self._me(request)
        sharing.revoke(profile, get_object_or_404(Organization, pk=org_id), by_speaker=True)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ pitches


class MyPitchViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    """Speaker self-service: submit pitches and see their status."""

    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        return s.PitchSubmitSerializer if self.action == "create" else s.MyPitchSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # OpenAPI generation
            return Pitch.objects.none()
        return Pitch.objects.filter(submitted_by__user=self.request.user).prefetch_related("targets__event")

    def create(self, request, *args, **kwargs):
        profile = SpeakerProfile.objects.filter(user=request.user).first()
        if profile is None:
            raise ValidationError("Create your speaker profile first (GET /api/v1/speakers/me/).")
        body = s.PitchSubmitSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        d = dict(body.validated_data)
        answers = {int(k): v for k, v in (d.pop("answers", None) or {}).items()}
        try:
            pitch = pitch_services.submit_pitch(
                submitter=profile,
                user=request.user,
                events=d.pop("events"),
                co_speakers=d.pop("co_speakers", []),
                answers_by_event=answers,
                **d,
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict if hasattr(exc, "error_dict") else exc.messages) from exc
        return Response(s.MyPitchSerializer(pitch).data, status=status.HTTP_201_CREATED)


class PitchTargetViewSet(OrgScopedViewSet):
    """Staff review of pitches (the review dashboard's data)."""

    queryset = PitchTarget.objects.select_related("pitch__submitted_by", "event").prefetch_related("votes__user")
    serializer_class = s.PitchTargetSerializer
    filterset_fields = ["event", "status"]
    search_fields = ["pitch__title", "pitch__description"]
    http_method_names = ["get", "patch", "post", "head", "options"]

    def _targets(self, ids):
        targets = list(self.get_queryset().filter(pk__in=ids))
        for t in targets:
            self._check_write(t)
        return targets

    @action(detail=False, methods=["post"])
    def decide(self, request):
        """Queue accept/decline/waitlist for several targets (no notification yet)."""
        body = s.DecisionSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        n = pitch_services.queue_decision(
            self._targets(body.validated_data["ids"]), body.validated_data["decision"], request.user
        )
        return Response({"updated": n})

    @action(detail=False, methods=["post"])
    def inform(self, request):
        """Finalize queued decisions (creates sessions for accepts)."""
        return Response(pitch_services.inform(self._targets(request.data.get("ids", []))))

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        target = self.get_object()
        self._check_write(target)
        session = pitch_services.accept(target, request.user)
        return Response({"session": session.pk}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def merge(self, request, pk=None):
        primary = self.get_object()
        n = pitch_services.merge(primary, self._targets(request.data.get("ids", [])), request.user)
        return Response({"merged": n})

    @action(detail=True, methods=["post"])
    def vote(self, request, pk=None):
        target = self.get_object()
        if not has_role(request.user, target.event.organization, Role.STAFF):
            raise PermissionDenied
        body = s.VoteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        PitchVote.objects.update_or_create(target=target, user=request.user, defaults=body.validated_data)
        return Response(s.PitchTargetSerializer(target, context={"request": request}).data)


# ------------------------------------------------------------------ commerce


class CompanyViewSet(viewsets.ModelViewSet):
    queryset = Company.objects.all()
    serializer_class = s.CompanySerializer
    permission_classes = [SharedDirectoryPermission]
    search_fields = ["name"]


class SponsorshipViewSet(OrgScopedViewSet):
    queryset = Sponsorship.objects.select_related("company", "level")
    serializer_class = s.SponsorshipSerializer
    filterset_fields = ["event", "scope", "status"]


class ExhibitorBookingViewSet(OrgScopedViewSet):
    queryset = ExhibitorBooking.objects.select_related("company", "package")
    serializer_class = s.ExhibitorBookingSerializer
    filterset_fields = ["event", "status"]


# ------------------------------------------------------------------ publishing, reports, assistant


class PublishRunViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = s.PublishRunSerializer
    filterset_fields = ["event", "status"]

    def get_queryset(self):
        return PublishRun.objects.for_user(self.request.user)


@extend_schema_view(
    list=extend_schema(responses=OpenApiTypes.OBJECT),
    retrieve=extend_schema(
        responses=OpenApiTypes.OBJECT,
        parameters=[
            OpenApiParameter("id", str, OpenApiParameter.PATH, description="Report slug"),
            OpenApiParameter("series", int, description="Series id (series-scoped reports)"),
            OpenApiParameter("event", int, description="Event id (event-scoped reports)"),
        ],
    ),
)
class ReportViewSet(viewsets.ViewSet):
    """GET /reports/ lists reports; GET /reports/<slug>/?series=<id> (or
    ?event=<id>) runs one. Only data from the user's organizations."""

    def list(self, request):
        return Response(
            [
                {"slug": r.slug, "title": r.title, "description": r.description, "scope": r.scope}
                for r in all_reports().values()
            ]
        )

    def retrieve(self, request, pk=None):
        report = all_reports().get(pk)
        if report is None:
            raise NotFound
        model = {ReportScope.SERIES: EventSeries, ReportScope.EVENT: Event, ReportScope.ORGANIZATION: Organization}[
            report.scope
        ]
        obj_id = request.query_params.get(str(report.scope))
        if not obj_id:
            raise ValidationError(f"Pass ?{report.scope}=<id>.")
        obj = get_object_or_404(model.objects.for_user(request.user), pk=obj_id)
        return Response(report.compute(obj, **request.query_params.dict()))


@extend_schema_view(create=extend_schema(request=s.AskSerializer, responses=OpenApiTypes.OBJECT))
class AskViewSet(viewsets.ViewSet):
    """POST /ask/ {organization, question} → natural-language answer (if an LLM is configured)."""

    def create(self, request):
        body = s.AskSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        org = body.validated_data["organization"]
        if not has_role(request.user, org, Role.VIEWER):
            raise PermissionDenied
        try:
            answer = QueryService(org).ask(body.validated_data["question"])
        except LLMNotConfigured as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_501_NOT_IMPLEMENTED)
        return Response(
            {
                "answer": answer.text,
                "provider": answer.provider,
                "model": answer.model,
                "tool_calls": [c.__dict__ for c in answer.tool_calls],
            }
        )


class PublicEventViewSet(viewsets.ReadOnlyModelViewSet):
    """Unauthenticated list of PUBLIC upcoming events (for apps/aggregators).
    Full schedules: /feeds/<org>/<event>/bundle.json."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    serializer_class = s.CalendarEventSerializer

    def get_queryset(self):
        return Event.objects.upcoming().public().select_related("organization").order_by("start_date")
