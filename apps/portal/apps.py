from django.apps import AppConfig


class PortalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.portal"
    verbose_name = "Speaker portal"

    def ready(self):
        from . import signals  # noqa: F401
