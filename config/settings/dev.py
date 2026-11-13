"""Local development settings."""

from .base import *  # noqa: F403
from .base import env

DEBUG = env.bool("DEBUG", default=True)
SECRET_KEY = env("SECRET_KEY", default="dev-only-insecure-secret-key-change-me")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
ACCOUNT_EMAIL_VERIFICATION = env("ACCOUNT_EMAIL_VERIFICATION", default="none")
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# A fixed dev key so encrypted integration credentials survive restarts.
# NEVER use this outside local development.
FIELD_ENCRYPTION_KEYS = env.list(
    "FIELD_ENCRYPTION_KEYS", default=["ZGV2LW9ubHktZmllbGQtZW5jcnlwdGlvbi1rZXktMDA="]
)

STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}  # noqa: F405
