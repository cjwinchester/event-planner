"""Base settings shared by every environment.

Everything environment-specific is read from environment variables (or a
local `.env` file — see `.env.example`). `dev.py`, `test.py` and `prod.py`
import from here and override only what differs.
"""

from __future__ import annotations

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SECRET_KEY = env("SECRET_KEY", default="")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# GeoDjango needs GDAL + GEOS. On macOS/Homebrew Django usually finds them;
# set these when it doesn't (e.g. /opt/homebrew/lib/libgdal.dylib).
GDAL_LIBRARY_PATH = env("GDAL_LIBRARY_PATH", default=None)
GEOS_LIBRARY_PATH = env("GEOS_LIBRARY_PATH", default=None)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.gis",
    "django.contrib.postgres",
    # Third party
    "rest_framework",
    "rest_framework.authtoken",
    "django_filters",
    "drf_spectacular",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "django_tasks_db",
    # Local apps — see README "Architecture" for what each one owns.
    "apps.core",
    "apps.accounts",
    "apps.organizations",
    "apps.venues",
    "apps.events",
    "apps.program",
    "apps.speakers",
    "apps.pitches",
    "apps.commerce",
    "apps.integrations",
    "apps.publishing",
    "apps.reporting",
    "apps.assistant",
    "apps.api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
SITE_ID = 1

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# PostGIS is required (venues, attendee geography). `postgis://` URLs map to
# django.contrib.gis.db.backends.postgis.
DATABASES = {
    "default": env.db("DATABASE_URL", default="postgis://postgres@localhost:5432/event_planner"),
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"  # Stored datetimes are UTC; each Event carries its own IANA timezone.
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = env("MEDIA_URL", default="/media/")
MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(BASE_DIR / "media")))

# --- File storage ---------------------------------------------------------
# Three storages:
#   default  — public-ish media (headshots, logos, floor plans, session assets)
#   private  — never publicly readable (hotel contracts); served via signed URLs
#   publish  — where static schedule exports land when no S3 integration is
#              configured for an organization (local dev)
# With AWS_STORAGE_BUCKET_NAME set, default/private move to S3.
AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME", default="")
AWS_PRIVATE_STORAGE_BUCKET_NAME = env("AWS_PRIVATE_STORAGE_BUCKET_NAME", default="")
AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default=None)

if AWS_STORAGE_BUCKET_NAME:
    _default_storage = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {"bucket_name": AWS_STORAGE_BUCKET_NAME, "querystring_auth": False},
    }
    _private_storage = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": AWS_PRIVATE_STORAGE_BUCKET_NAME or AWS_STORAGE_BUCKET_NAME,
            "location": "private",
            "default_acl": "private",
            "querystring_auth": True,
            "querystring_expire": 300,
        },
    }
else:
    _default_storage = {"BACKEND": "django.core.files.storage.FileSystemStorage"}
    _private_storage = {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(BASE_DIR / "private_media"), "base_url": "/private-media/"},
    }

STORAGES = {
    "default": _default_storage,
    "private": _private_storage,
    "publish": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(BASE_DIR / "published"), "base_url": "/published/"},
    },
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --- Background tasks (Django's built-in Tasks framework) -----------------
# django_tasks_db stores the queue in Postgres; run `manage.py db_worker`.
# Swapping to another backend is a settings change — task code only uses
# django.tasks' `@task` / `.enqueue()`.
TASKS = {
    "default": {
        "BACKEND": env("TASKS_BACKEND", default="django_tasks_db.DatabaseBackend"),
        "QUEUES": ["default"],
    }
}

# --- Auth: django-allauth (email login + Google Workspace SSO) ------------
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_EMAIL_VERIFICATION = env("ACCOUNT_EMAIL_VERIFICATION", default="mandatory")
ACCOUNT_UNIQUE_EMAIL = True
SOCIALACCOUNT_ADAPTER = "apps.accounts.adapters.SocialAccountAdapter"
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APPS": [
            {
                "client_id": env("GOOGLE_OAUTH_CLIENT_ID", default=""),
                "secret": env("GOOGLE_OAUTH_CLIENT_SECRET", default=""),
                "key": "",
            }
        ],
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
        "EMAIL_AUTHENTICATION": True,
    }
}
LOGIN_REDIRECT_URL = "/"

# --- REST API -------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 100,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}
SPECTACULAR_SETTINGS = {
    "TITLE": "Event Planner API",
    "DESCRIPTION": "Multi-tenant event planning for nonprofits. See docs/api.md.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

# --- Secrets at rest ------------------------------------------------------
# Fernet keys (urlsafe base64, 32 bytes) used by core.fields.EncryptedJSONField
# for integration credentials. First key encrypts; all keys decrypt, so keys
# can be rotated by prepending a new one.
FIELD_ENCRYPTION_KEYS = env.list("FIELD_ENCRYPTION_KEYS", default=[])

# --- App settings ---------------------------------------------------------
EVENT_PLANNER = {
    # Base URL that locally published files are served from (dev only).
    "LOCAL_PUBLISH_BASE_URL": env("LOCAL_PUBLISH_BASE_URL", default="http://localhost:8000/published/"),
    # Contact identifier sent to third-party APIs that ask for one (geocoders).
    "HTTP_USER_AGENT": env("HTTP_USER_AGENT", default="event-planner (open-source; +https://github.com)"),
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}
