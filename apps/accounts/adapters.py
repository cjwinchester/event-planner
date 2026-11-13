"""django-allauth adapters: Google Workspace SSO → organization membership."""

from __future__ import annotations

import logging

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

logger = logging.getLogger(__name__)


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """After a Google login, auto-join organizations that opted in.

    An organization opts in by listing its Workspace domain in
    `Organization.sso_domains` *and* setting `sso_default_role`. Google's
    `hd` (hosted domain) claim is used rather than the email suffix, so a
    personal Gmail address can never match a Workspace domain.
    Existing memberships are never changed.
    """

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)
        self.join_sso_organizations(user, sociallogin)
        return user

    def pre_social_login(self, request, sociallogin):
        super().pre_social_login(request, sociallogin)
        if sociallogin.is_existing:
            self.join_sso_organizations(sociallogin.user, sociallogin)

    @staticmethod
    def join_sso_organizations(user, sociallogin) -> list:
        from apps.organizations.models import Membership, Organization

        if sociallogin.account.provider != "google":
            return []
        hosted_domain = (sociallogin.account.extra_data or {}).get("hd", "").lower()
        if not hosted_domain:
            return []
        joined = []
        for org in Organization.objects.filter(sso_domains__contains=[hosted_domain], is_active=True):
            if not org.sso_default_role:
                continue
            _, created = Membership.objects.get_or_create(
                user=user, organization=org, defaults={"role": org.sso_default_role}
            )
            if created:
                logger.info("SSO auto-joined %s to %s as %s", user, org, org.sso_default_role)
                joined.append(org)
        return joined
