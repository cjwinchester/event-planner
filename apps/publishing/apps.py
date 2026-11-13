from django.apps import AppConfig


class PublishingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.publishing"
    verbose_name = "Publishing"

    def ready(self):
        # Built-in exporters register themselves on import.
        from . import exporters  # noqa: F401
