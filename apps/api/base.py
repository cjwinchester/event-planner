"""Shared API plumbing: tenancy-aware permissions, viewsets and serializers.

Rules (mirroring core.admin so the admin and API can never disagree):
  * querysets: `Model.objects.for_user(user)` (superusers see all);
  * reads need VIEWER in the object's organization, writes need STAFF
    (`write_role` per viewset; ADMIN for integrations);
  * related fields in request bodies only accept objects from the user's
    organizations (OrgScopedSerializerMixin);
  * create/update re-check the role against the organization the object
    ends up in, inside a transaction, so nothing can be moved into another
    org;
  * model `clean()` runs on every API save (OrgModelSerializer), so model
    validation — including scheduling conflict checks — applies here too.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import permissions, serializers, viewsets
from rest_framework.exceptions import PermissionDenied

from apps.core.models import resolve_organization
from apps.organizations.models import Membership, Organization
from apps.organizations.permissions import has_role, organization_ids_for

Role = Membership.Role


class OrgRolePermission(permissions.BasePermission):
    """Object-level role check against the object's organization."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        org = resolve_organization(obj)
        if org is None:
            return request.user.is_superuser
        needed = Role.VIEWER if request.method in permissions.SAFE_METHODS else getattr(view, "write_role", Role.STAFF)
        return has_role(request.user, org, needed)


class SharedDirectoryPermission(permissions.BasePermission):
    """Global directories: members read; staff of any org write; superusers delete."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return user.is_superuser or bool(organization_ids_for(user, Role.VIEWER))
        if request.method == "DELETE":
            return user.is_superuser
        return user.is_superuser or bool(organization_ids_for(user, Role.STAFF))


class OrgScopedSerializerMixin:
    """Narrow every related-field queryset to the requesting user's orgs."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or user.is_superuser:
            return
        for field in self.fields.values():
            target = getattr(field, "child_relation", field)
            qs = getattr(target, "queryset", None)
            if qs is None:
                continue
            model = qs.model
            if model is Organization:
                target.queryset = qs.filter(pk__in=organization_ids_for(user, Role.STAFF))
            elif getattr(model, "ORG_PATH", None):
                target.queryset = qs.filter(**{f"{model.ORG_PATH}__in": organization_ids_for(user, Role.VIEWER)})


class OrgModelSerializer(OrgScopedSerializerMixin, serializers.ModelSerializer):
    """ModelSerializer that runs the model's clean() before saving."""

    def validate(self, attrs):
        attrs = super().validate(attrs)
        model = self.Meta.model
        m2m = {f.name for f in model._meta.many_to_many}
        instance = self.instance or model()
        original = {k: getattr(instance, k) for k in attrs if k not in m2m and hasattr(instance, k)}
        for key, value in attrs.items():
            if key not in m2m:
                setattr(instance, key, value)
        try:
            instance.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.message_dict if hasattr(exc, "error_dict") else {"non_field_errors": exc.messages}
            ) from exc
        finally:
            if self.instance is not None:  # don't leave unsaved changes on the live instance
                for key, value in original.items():
                    setattr(instance, key, value)
        return attrs


class OrgScopedViewSet(viewsets.ModelViewSet):
    permission_classes = [OrgRolePermission]
    write_role = Role.STAFF

    def get_queryset(self):
        qs = self.queryset.all()
        user = self.request.user
        if user.is_superuser:
            return qs
        return qs.filter(**{f"{qs.model.ORG_PATH}__in": organization_ids_for(user, Role.VIEWER)})

    def _check_write(self, obj):
        org = resolve_organization(obj)
        if org is None or not has_role(self.request.user, org, self.write_role):
            raise PermissionDenied("You can't change data for that organization.")

    def perform_create(self, serializer):
        with transaction.atomic():
            self._check_write(serializer.save())

    def perform_update(self, serializer):
        with transaction.atomic():
            self._check_write(serializer.save())

    def perform_destroy(self, instance):
        self._check_write(instance)
        instance.delete()
