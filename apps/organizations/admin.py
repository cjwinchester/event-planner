from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model

from apps.core.admin import OrgScopedAdmin, OrgScopedInline

from .models import Membership, Organization

Role = Membership.Role


class MembershipForm(forms.ModelForm):
    """Org admins add colleagues by email. An unknown email creates an
    inactive-password user who signs in via Google SSO or password reset."""

    email = forms.EmailField(help_text="The colleague's login email.")

    class Meta:
        model = Membership
        fields = ["email", "role", "title", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["email"].initial = self.instance.user.email
            self.fields["email"].disabled = True

    def save(self, commit=True):
        if not self.instance.pk:
            User = get_user_model()
            email = self.cleaned_data["email"].strip()
            user = User.objects.filter(email__iexact=email).first() or User.objects.create_user(email=email)
            self.instance.user = user
        return super().save(commit)


class MembershipInline(OrgScopedInline, admin.TabularInline):
    model = Membership
    form = MembershipForm
    extra = 0
    required_role = Role.ADMIN


@admin.register(Organization)
class OrganizationAdmin(OrgScopedAdmin):
    """Org admins edit their own organization (sharing defaults, SSO);
    only superusers create organizations (tenants)."""

    required_role = Role.ADMIN
    list_display = ["name", "slug", "default_event_visibility", "is_active"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [MembershipInline]
    fieldsets = (
        (None, {"fields": ("name", "slug", "website", "description", "logo", "default_timezone", "is_active")}),
        ("Sharing defaults", {"fields": ("default_event_visibility", "default_note_visibility")}),
        ("Google Workspace SSO", {"fields": ("sso_domains", "sso_default_role")}),
        ("Public IDs", {"fields": ("session_id_start",)}),
    )

    def has_add_permission(self, request, *args):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(Membership)
class MembershipAdmin(OrgScopedAdmin):
    required_role = Role.ADMIN
    form = MembershipForm
    list_display = ["user", "organization", "role", "title", "is_active"]
    list_filter = ["organization", "role", "is_active"]
    search_fields = ["user__email", "user__first_name", "user__last_name"]

    def get_fields(self, request, obj=None):
        return ["organization", "email", "role", "title", "is_active"]
