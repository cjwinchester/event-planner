"""Org-aware Django admin building blocks.

Phase 1 uses the Django admin as the staff UI, so tenancy must hold here:

`OrgScopedAdmin` (for models with ORG_PATH)
  * list/change views only show rows from the user's organizations;
  * FK/M2M dropdowns only offer the user's organizations' rows;
  * permissions come from Membership roles, not Django model perms:
      VIEWER → view; STAFF → add/change/delete; ADMIN-only models set
      `required_role = Role.ADMIN` (integrations, memberships);
  * a new object's organization is defaulted when the user has one org.

`SharedDirectoryAdmin` (global models: Venue, Room, SpeakerProfile, Company)
  * every org member can view/add/change; only superusers delete.

`EventTimezoneMixin`
  * renders/accepts datetimes in the event's own timezone on change forms,
    so planners type local wall-clock times, never UTC.

Superusers bypass all scoping.
"""

from __future__ import annotations

import zoneinfo

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.core.models import resolve_organization
from apps.organizations.models import Membership, Organization
from apps.organizations.permissions import has_role, organization_ids_for

Role = Membership.Role


def _scope_related_queryset(db_field, request, kwargs):
    user = request.user
    if user.is_superuser or "queryset" in kwargs:
        return
    related = db_field.related_model
    if related is get_user_model():
        # Only colleagues: members of the user's own organizations.
        kwargs["queryset"] = related.objects.filter(
            memberships__organization_id__in=organization_ids_for(user, Role.VIEWER), memberships__is_active=True
        ).distinct()
    elif related is Organization:
        kwargs["queryset"] = Organization.objects.filter(pk__in=organization_ids_for(user, Role.STAFF))
    elif getattr(related, "ORG_PATH", None):
        kwargs["queryset"] = related._default_manager.filter(
            **{f"{related.ORG_PATH}__in": organization_ids_for(user, Role.VIEWER)}
        )


class _OrgScopingMixin:
    required_role = Role.STAFF  # to add/change/delete

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        path = self.model.ORG_PATH
        return qs.filter(**{f"{path}__in": organization_ids_for(request.user, Role.VIEWER)})

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        _scope_related_queryset(db_field, request, kwargs)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        _scope_related_queryset(db_field, request, kwargs)
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    # --- role-based permissions ------------------------------------------
    def _org_of(self, obj):
        return resolve_organization(obj) if obj is not None else None

    def _has(self, request, obj, role) -> bool:
        user = request.user
        if not (user.is_active and user.is_authenticated):
            return False
        if user.is_superuser:
            return True
        org = self._org_of(obj)
        if org is not None:
            return has_role(user, org, role)
        return bool(organization_ids_for(user, role))

    def has_module_permission(self, request):
        return self._has(request, None, Role.VIEWER)

    def has_view_permission(self, request, obj=None):
        return self._has(request, obj, Role.VIEWER)

    def has_add_permission(self, request, *args):
        return self._has(request, None, self.required_role)

    def has_change_permission(self, request, obj=None):
        return self._has(request, obj, self.required_role)

    def has_delete_permission(self, request, obj=None):
        return self._has(request, obj, self.required_role)


class OrgScopedAdmin(_OrgScopingMixin, admin.ModelAdmin):
    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        if "organization" in [f.name for f in self.model._meta.fields]:
            orgs = organization_ids_for(request.user, Role.STAFF)
            if len(orgs) == 1:
                initial.setdefault("organization", orgs[0])
        return initial


class OrgScopedInline(_OrgScopingMixin):
    """Mixin for inlines (combine with admin.TabularInline/StackedInline).

    Inline permissions follow the *parent* object's organization."""

    def has_add_permission(self, request, obj=None):
        return self._has(request, obj, self.required_role)

    def _org_of(self, obj):
        return resolve_organization(obj) if obj is not None else None


class SharedDirectoryAdmin(admin.ModelAdmin):
    """Global directory models: any org member may view/add/change."""

    def _is_member(self, request) -> bool:
        user = request.user
        return user.is_active and (user.is_superuser or bool(organization_ids_for(user, Role.VIEWER)))

    def _is_editor(self, request) -> bool:
        user = request.user
        return user.is_active and (user.is_superuser or bool(organization_ids_for(user, Role.STAFF)))

    def has_module_permission(self, request):
        return self._is_member(request)

    def has_view_permission(self, request, obj=None):
        return self._is_member(request)

    def has_add_permission(self, request, *args):
        return self._is_editor(request)

    def has_change_permission(self, request, obj=None):
        return self._is_editor(request)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        _scope_related_queryset(db_field, request, kwargs)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not change and hasattr(obj, "created_by_organization_id") and not obj.created_by_organization_id:
            orgs = organization_ids_for(request.user, Role.STAFF)
            if orgs:
                obj.created_by_organization_id = orgs[0]
        super().save_model(request, obj, form, change)


class SharedDirectoryInline:
    """Mixin for inlines on shared-directory parents."""

    def _ok(self, request, role):
        user = request.user
        return user.is_active and (user.is_superuser or bool(organization_ids_for(user, role)))

    def has_view_permission(self, request, obj=None):
        return self._ok(request, Role.VIEWER)

    def has_add_permission(self, request, obj=None):
        return self._ok(request, Role.STAFF)

    def has_change_permission(self, request, obj=None):
        return self._ok(request, Role.STAFF)

    def has_delete_permission(self, request, obj=None):
        return self._ok(request, Role.STAFF)


class EventTimezoneMixin:
    """Activate the event's timezone while rendering/saving a change form.

    `event_path` is the attribute path from the object to its Event
    ("event" for Session, "session__event" for nested rows, "" for Event). On the add form
    (no object yet) the event can be pre-selected with ?event=<id>.
    """

    event_path = "event"

    def _event_for(self, request, object_id):
        from apps.events.models import Event

        if object_id:
            obj = self.get_object(request, object_id)
            if not self.event_path:  # the object *is* the event
                return obj
            current = obj
            for part in self.event_path.split("__"):
                current = getattr(current, part, None) if current is not None else None
            return current
        event_id = request.GET.get("event") or request.POST.get("event")
        if event_id and str(event_id).isdigit():
            return Event.objects.filter(pk=event_id).first()
        return None

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        event = self._event_for(request, object_id)
        if event is None:
            return super().changeform_view(request, object_id, form_url, extra_context)
        extra_context = {**(extra_context or {}), "event_timezone": event.timezone}
        with timezone.override(zoneinfo.ZoneInfo(event.timezone)):
            return super().changeform_view(request, object_id, form_url, extra_context)
