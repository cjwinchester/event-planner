from django.apps import AppConfig


class ReportingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reporting"
    verbose_name = "Reporting"

    def ready(self):
        # Built-in reports register themselves on import (see registry.py).
        from . import geo, pace  # noqa: F401
