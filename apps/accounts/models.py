"""User accounts.

One `User` model for every kind of person who logs in: organization staff,
speakers, and sponsor/exhibitor contacts. What a user can *do* comes from
relationships, not from user subclasses:

- `organizations.Membership` (role per organization) → staff / org admin
- `speakers.SpeakerProfile.user`                     → speaker self-service
- `commerce.CompanyContact.user`                      → sponsor/exhibitor portal
- `is_superuser`                                      → installation super admin

Email is the login identifier (allauth is configured for email-only login,
plus Google Workspace SSO).
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if not extra_fields["is_staff"] or not extra_fields["is_superuser"]:
            raise ValueError("Superuser must have is_staff=True and is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Email-identified user. `is_staff` (Django admin access) is kept in
    sync with organization membership by organizations.signals — org
    members get admin access; what they see there is org-scoped."""

    username = None
    email = models.EmailField("email address", unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        ordering = ["email"]
        constraints = [
            models.UniqueConstraint(Lower("email"), name="unique_user_email_ci"),
        ]

    def __str__(self) -> str:
        return self.get_full_name() or self.email
