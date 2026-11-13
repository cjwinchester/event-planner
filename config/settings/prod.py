"""Production settings. Everything secret comes from the environment."""

from .base import *  # noqa: F403
from .base import FIELD_ENCRYPTION_KEYS, SECRET_KEY, env

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY must be set in production")
if not FIELD_ENCRYPTION_KEYS:
    raise RuntimeError("FIELD_ENCRYPTION_KEYS must be set in production")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 30)
MAILERS = {"default": {"BACKEND": env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")}}
