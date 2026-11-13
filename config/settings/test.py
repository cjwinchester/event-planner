"""Test settings (pytest uses these — see pyproject.toml)."""

import os
import tempfile

from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "test-secret-key"
ALLOWED_HOSTS = ["testserver", "localhost"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
ACCOUNT_EMAIL_VERIFICATION = "none"
FIELD_ENCRYPTION_KEYS = ["dGVzdC1vbmx5LWZpZWxkLWVuY3J5cHQta2V5LTAwMDA="]

# Run tasks inline so tests can assert on their effects.
TASKS = {"default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}}

_FS = "django.core.files.storage.FileSystemStorage"
_tmp = tempfile.mkdtemp(prefix="event-planner-tests-")
MEDIA_ROOT = _tmp + "/media"
STATIC_ROOT = _tmp + "/static"
os.makedirs(STATIC_ROOT, exist_ok=True)
STORAGES = {
    "default": {"BACKEND": _FS, "OPTIONS": {"location": MEDIA_ROOT}},
    "private": {"BACKEND": _FS, "OPTIONS": {"location": _tmp + "/private"}},
    "publish": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": _tmp + "/published", "base_url": "http://testserver/published/"},
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
