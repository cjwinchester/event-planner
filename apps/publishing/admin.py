from django.contrib import admin
from django.utils.html import format_html_join

from apps.core.admin import OrgScopedAdmin

from .models import PublishRun


@admin.register(PublishRun)
class PublishRunAdmin(OrgScopedAdmin):
    list_display = ["event", "status", "target", "session_count", "dry_run", "created_at", "finished_at"]
    list_filter = ["event__organization", "event", "status"]
    readonly_fields = [f.name for f in PublishRun._meta.fields] + ["file_links"]

    @admin.display(description="Files")
    def file_links(self, obj):
        return format_html_join(
            "",
            '<p><a href="{}">{}</a> ({} bytes)</p>',
            ((f.get("url"), f.get("path"), f.get("bytes")) for f in obj.files),
        )

    def has_add_permission(self, request, *args):
        return False

    def has_change_permission(self, request, obj=None):
        return False
