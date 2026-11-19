"""Who at a company sees what, and who may change shared company records.

See CompanyContact for the model. Two layers, as for speakers:
  * the shared company profile (name, website, description, logo) is read by
    every organization; once the company has a company-wide manager using the
    portal it belongs to the company, and only its company-wide managers edit it;
  * deals (sponsorships, bookings, invoices, deliverables, exhibitor answers)
    are private between one organization and the company: a manager sees those
    for the organizations in their scope, or all if company-wide; members none.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Lower

from .models import Company, CompanyContact, ExhibitorBooking, Sponsorship


def deal_q(contact: CompanyContact) -> Q | None:
    """Filter for the deals `contact` may see (None: none at all)."""
    if not contact.can_manage:
        return None
    q = Q(company_id=contact.company_id)
    if contact.company_wide:
        return q
    return q & Q(event__organization_id__in=list(contact.organizations.values_list("pk", flat=True)))


def visible(qs, contact: CompanyContact):
    q = deal_q(contact)
    return qs.none() if q is None else qs.filter(q)


def is_claimed(company: Company) -> bool:
    return company.contacts.filter(company_wide=True, can_manage=True, user__isnull=False).exists()


def can_edit_company(user, company: Company, org_ids=()) -> bool:
    """Company-wide managers always; organizations (staff in `org_ids`) only while
    the company hasn't been claimed, and only if they do business with it."""
    if user.is_superuser:
        return True
    if company.contacts.filter(user=user, company_wide=True, can_manage=True).exists():
        return True
    if is_claimed(company):
        return False
    return (
        Sponsorship.objects.filter(company=company, event__organization_id__in=org_ids).exists()
        or ExhibitorBooking.objects.filter(company=company, event__organization_id__in=org_ids).exists()
        or company.created_by_organization_id in set(org_ids)
    )


def _deals_elsewhere(company: Company, organization) -> bool:
    return (
        Sponsorship.objects.filter(company=company).exclude(event__organization=organization).exists()
        or ExhibitorBooking.objects.filter(company=company).exclude(event__organization=organization).exists()
    )


def add_contact_for_org(company: Company, organization, *, name: str, email: str, manager: bool) -> CompanyContact:
    """Staff at `organization` add (or re-add) a person. They only ever add their own
    organization to the person's scope. The company's first manager becomes
    company-wide, unless the company already does business with other
    organizations (whose deals this organization must not expose)."""
    with transaction.atomic():
        Company.objects.select_for_update().get(pk=company.pk)  # serialize "first manager" decisions
        contact = company.contacts.annotate(e=Lower("email")).filter(e=email.strip().lower()).first() or CompanyContact(
            company=company, name=name, email=email.strip(), can_manage=False
        )
        first_manager = manager and not company.contacts.filter(can_manage=True).exclude(pk=contact.pk).exists()
        if manager:
            contact.can_manage = True
        if first_manager and not _deals_elsewhere(company, organization):
            contact.company_wide = True
        contact.is_primary = contact.is_primary or not company.contacts.exclude(pk=contact.pk).exists()
        contact.save()
        contact.organizations.add(organization)
    return contact


def can_add_colleague(me: CompanyContact) -> bool:
    return me.can_manage


def scope_for_new_colleague(me: CompanyContact, contact: CompanyContact, *, company_wide: bool) -> None:
    """A manager adding a colleague passes on at most their own scope."""
    if company_wide and me.company_wide:
        contact.company_wide = True
        contact.save(update_fields=["company_wide", "updated_at"])
    elif not me.company_wide:
        contact.organizations.add(*me.organizations.all())
    else:
        # A company-wide manager adding a scoped colleague: scope to orgs the company works with.
        orgs = set(Sponsorship.objects.filter(company=me.company).values_list("event__organization_id", flat=True))
        orgs |= set(
            ExhibitorBooking.objects.filter(company=me.company).values_list("event__organization_id", flat=True)
        )
        contact.organizations.add(*orgs)


__all__ = [
    "add_contact_for_org", "can_add_colleague", "can_edit_company", "deal_q", "is_claimed",
    "scope_for_new_colleague", "visible",
]  # fmt: skip
