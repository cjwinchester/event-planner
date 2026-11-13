from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q

from apps.core.admin import OrgScopedAdmin, OrgScopedInline
from apps.organizations.permissions import organization_ids_for

from . import services
from .models import FormQuestion, Pitch, PitchSpeaker, PitchTarget, PitchVote

Status = PitchTarget.Status


class PitchVoteInline(OrgScopedInline, admin.TabularInline):
    model = PitchVote
    extra = 0
    fields = ["user", "vote", "comment"]


@admin.register(PitchTarget)
class PitchTargetAdmin(OrgScopedAdmin):
    """The review dashboard (phase 1): filter by event/status, vote, then use
    the actions — queue decisions, *then* "Inform" to finalize and notify."""

    list_display = ["pitch", "submitter", "event", "status", "yes_votes", "no_votes", "informed_at", "session"]
    list_filter = ["event__organization", "event", "status", "suggested_tracks"]
    search_fields = ["pitch__title", "pitch__description", "pitch__submitted_by__last_name"]
    filter_horizontal = ["suggested_tracks"]
    readonly_fields = [
        "pitch", "event", "decided_by", "decided_at", "informed_at", "confirmed_at", "session", "merged_into",
    ]  # fmt: skip
    inlines = [PitchVoteInline]
    actions = ["queue_accept", "queue_decline", "waitlist", "inform_selected", "accept_now", "merge_into_first"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("pitch__submitted_by", "event", "session")
            .annotate(
                _yes=Count("votes", filter=Q(votes__vote="yes")),
                _no=Count("votes", filter=Q(votes__vote="no")),
            )
        )

    @admin.display(description="Submitted by", ordering="pitch__submitted_by__last_name")
    def submitter(self, obj):
        return obj.pitch.submitted_by

    @admin.display(description="Yes", ordering="_yes")
    def yes_votes(self, obj):
        return obj._yes

    @admin.display(description="No", ordering="_no")
    def no_votes(self, obj):
        return obj._no

    def _run(self, request, fn, label):
        try:
            result = fn()
        except ValidationError as exc:
            self.message_user(request, f"{label} failed: {'; '.join(exc.messages)}", messages.ERROR)
            return
        self.message_user(request, f"{label}: {result}", messages.SUCCESS)

    @admin.action(description="Queue: accept (doesn't notify yet)")
    def queue_accept(self, request, queryset):
        self._run(request, lambda: services.queue_decision(queryset, Status.ACCEPT_QUEUE, request.user), "Queued")

    @admin.action(description="Queue: decline (doesn't notify yet)")
    def queue_decline(self, request, queryset):
        self._run(request, lambda: services.queue_decision(queryset, Status.DECLINE_QUEUE, request.user), "Queued")

    @admin.action(description="Waitlist")
    def waitlist(self, request, queryset):
        self._run(request, lambda: services.queue_decision(queryset, Status.WAITLISTED, request.user), "Waitlisted")

    @admin.action(description="Inform: finalize queued decisions (creates sessions for accepts)")
    def inform_selected(self, request, queryset):
        self._run(request, lambda: services.inform(queryset), "Informed")

    @admin.action(description="Accept now (create session)")
    def accept_now(self, request, queryset):
        self._run(request, lambda: len([services.accept(t, request.user) for t in queryset]), "Sessions created")

    @admin.action(description="Merge selected into the first selected pitch")
    def merge_into_first(self, request, queryset):
        targets = list(queryset.order_by("pk"))
        if len(targets) < 2:
            self.message_user(request, "Select at least two pitches.", messages.WARNING)
            return
        self._run(request, lambda: services.merge(targets[0], targets[1:], request.user), "Merged")


class PitchSpeakerInline(admin.TabularInline):
    model = PitchSpeaker
    extra = 0
    autocomplete_fields = ["speaker"]


@admin.register(Pitch)
class PitchAdmin(admin.ModelAdmin):
    """Pitches are global; staff see those submitted to their org's events."""

    list_display = ["title", "submitted_by", "format", "submitted_at"]
    search_fields = ["title", "description"]
    inlines = [PitchSpeakerInline]
    autocomplete_fields = ["submitted_by"]

    def _orgs(self, request):
        return organization_ids_for(request.user)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(targets__event__organization_id__in=self._orgs(request)).distinct()

    def has_module_permission(self, request):
        return request.user.is_superuser or bool(self._orgs(request))

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(FormQuestion)
class FormQuestionAdmin(OrgScopedAdmin):
    list_display = ["prompt", "event", "form", "kind", "required", "sort_order"]
    list_filter = ["event__organization", "event", "form"]
    prepopulated_fields = {"slug": ["prompt"]}
