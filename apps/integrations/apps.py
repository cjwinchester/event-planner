from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.integrations"
    verbose_name = "Integrations"

    def ready(self):
        # Import every connector module so each one registers itself with
        # apps.integrations.registry. Adding a connector = adding a module
        # under apps/integrations/connectors/ and listing it there.
        from . import connectors  # noqa: F401
