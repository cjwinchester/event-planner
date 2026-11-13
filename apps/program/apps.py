from django.apps import AppConfig


class ProgramConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.program"
    verbose_name = "Program (sessions, tracks, logistics)"
