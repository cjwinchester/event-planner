import json

from django import forms
from django.contrib import admin, messages

from apps.core.admin import OrgScopedAdmin
from apps.organizations.models import Membership

from .models import ExternalIdentity, IntegrationConfig, SyncRun
from .registry import all_connectors, choices
from .services import test_integration


class IntegrationConfigForm(forms.ModelForm):
    """Secrets are write-only: the form shows which keys are set, never values.
    Leave the field blank to keep the stored credentials."""

    connector = forms.ChoiceField(choices=[])
    new_credentials = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": '{"password": "..."}'}),
        help_text="JSON object. Blank = keep current credentials. Keys are merged into the stored ones.",
    )

    class Meta:
        model = IntegrationConfig
        fields = ["organization", "event", "connector", "is_enabled", "options", "notes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["connector"].choices = choices()
        if self.instance.pk:
            keys = sorted((self.instance.credentials or {}).keys())
            self.fields["new_credentials"].help_text += f" Currently set: {', '.join(keys) or 'none'}."
        slug = self.initial.get("connector") or self.data.get("connector")
        connector = all_connectors().get(slug)
        if connector:
            described = "; ".join(
                f"{f.key} ({f.scope}{', secret' if f.secret else ''}{', optional' if not f.required else ''})"
                for f in connector.credential_fields
            )
            self.fields["new_credentials"].help_text += f" Expected: {described or 'none'}."

    def clean_new_credentials(self):
        raw = self.cleaned_data["new_credentials"].strip()
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f"Not valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise forms.ValidationError("Enter a JSON object.")
        return value

    def save(self, commit=True):
        new = self.cleaned_data.get("new_credentials")
        if new:
            self.instance.credentials = {**(self.instance.credentials or {}), **new}
        return super().save(commit)


@admin.register(IntegrationConfig)
class IntegrationConfigAdmin(OrgScopedAdmin):
    required_role = Membership.Role.ADMIN
    form = IntegrationConfigForm
    list_display = ["connector", "organization", "event", "is_enabled", "last_test_ok", "last_tested_at"]
    list_filter = ["organization", "connector", "is_enabled"]
    readonly_fields = ["last_tested_at", "last_test_ok", "last_test_message"]
    actions = ["test_selected"]

    def get_fields(self, request, obj=None):
        return [
            "organization", "event", "connector", "is_enabled", "new_credentials", "options", "notes",
            "last_tested_at", "last_test_ok", "last_test_message",
        ]  # fmt: skip

    @admin.action(description="Test connection")
    def test_selected(self, request, queryset):
        for config in queryset:
            try:
                status = test_integration(config)
            except Exception as exc:  # show any connector failure to the admin
                self.message_user(request, f"{config}: {exc}", messages.ERROR)
                continue
            level = messages.SUCCESS if status.ok else messages.WARNING
            self.message_user(request, f"{config}: {status.message}", level)


@admin.register(SyncRun)
class SyncRunAdmin(OrgScopedAdmin):
    list_display = ["connector", "operation", "event", "status", "started_at", "finished_at"]
    list_filter = ["organization", "connector", "status"]
    readonly_fields = [f.name for f in SyncRun._meta.fields]

    def has_add_permission(self, request, *args):
        return False


@admin.register(ExternalIdentity)
class ExternalIdentityAdmin(OrgScopedAdmin):
    list_display = ["connector", "external_id", "content_type", "object_id", "last_synced_at"]
    list_filter = ["organization", "connector", "content_type"]
    search_fields = ["external_id"]
