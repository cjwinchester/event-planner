"""Custom model fields."""

from __future__ import annotations

import json

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


def _fernet() -> MultiFernet:
    keys = getattr(settings, "FIELD_ENCRYPTION_KEYS", None) or []
    if not keys:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEYS is empty; cannot encrypt/decrypt secrets")
    return MultiFernet([Fernet(k) for k in keys])


class EncryptedJSONField(models.TextField):
    """A JSON object encrypted at rest with Fernet (settings.FIELD_ENCRYPTION_KEYS).

    Used for integration credentials. The database only ever holds
    ciphertext; Python code sees a plain dict. Not queryable by content —
    by design. Rotate keys by prepending a new key to FIELD_ENCRYPTION_KEYS
    and re-saving rows (`MultiFernet.rotate`).
    """

    description = "Encrypted JSON object"

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("default", dict)
        kwargs.setdefault("blank", True)
        super().__init__(*args, **kwargs)

    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return {}
        try:
            return json.loads(_fernet().decrypt(value.encode()).decode())
        except InvalidToken as exc:
            raise ImproperlyConfigured(
                f"Could not decrypt {self.model.__name__}.{self.name}: wrong FIELD_ENCRYPTION_KEYS?"
            ) from exc

    def to_python(self, value):
        if isinstance(value, dict) or value is None:
            return value or {}
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return {}
        return value

    def get_prep_value(self, value):
        if value is None:
            value = {}
        return _fernet().encrypt(json.dumps(value, sort_keys=True).encode()).decode()

    def value_to_string(self, obj):
        # Never serialize secrets in dumpdata/fixtures.
        return ""
