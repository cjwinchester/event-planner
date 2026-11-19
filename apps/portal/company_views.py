"""Company pages in the portal: a contact's view of each company they work for.

A user reaches a company only through their own `CompanyContact` row.
Contacts with `can_manage` edit the company profile (shared by every
organization the company works with) and manage colleagues; other contacts
can only view.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.commerce.claiming import claim_contacts
from apps.commerce.models import CompanyContact

from .forms import CompanyForm, ContactForm
from .views import _ctx


def my_contact(request, slug, *, manage: bool = False) -> CompanyContact:
    # Pick up contacts staff added since this user logged in (normally linked at login).
    if not CompanyContact.objects.filter(user=request.user, company__slug=slug).exists():
        claim_contacts(request.user)
    contact = get_object_or_404(CompanyContact.objects.select_related("company"), user=request.user, company__slug=slug)
    if manage and not contact.can_manage:
        raise PermissionDenied("Only contacts who can manage this company can do that.")
    return contact


@login_required
def company(request, slug):
    me = my_contact(request, slug)
    return render(request, "portal/company.html", _ctx(request, me=me, company=me.company, **_deals(me.company)))


def _deals(company):
    """Filled in by part B (sponsorships and exhibitor bookings)."""
    return {}


@login_required
def company_edit(request, slug):
    me = my_contact(request, slug, manage=True)
    form = CompanyForm(request.POST or None, request.FILES or None, instance=me.company)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Company profile saved.")
        return redirect("portal:company", slug=me.company.slug)
    return render(
        request,
        "portal/company_edit.html",
        _ctx(request, me=me, company=me.company, form=form),
        status=422 if request.method == "POST" else 200,
    )


@login_required
def company_contacts(request, slug):
    me = my_contact(request, slug)
    form = ContactForm(request.POST or None, company=me.company)
    if request.method == "POST":
        if not me.can_manage:
            raise PermissionDenied
        if form.is_valid():
            contact = form.save(commit=False)
            contact.company = me.company
            contact.save()
            messages.success(
                request, f"Added {contact.name}. They can log in with {contact.email} to see this company."
            )
            return redirect("portal:company-contacts", slug=me.company.slug)
    contacts = me.company.contacts.select_related("user")
    return render(
        request,
        "portal/company_contacts.html",
        _ctx(request, me=me, company=me.company, contacts=contacts, form=form),
        status=422 if request.method == "POST" else 200,
    )


@login_required
@require_POST
def company_contact_update(request, slug, pk):
    me = my_contact(request, slug, manage=True)
    contact = get_object_or_404(CompanyContact, pk=pk, company=me.company)
    action = request.POST.get("action")
    removing_self = action == "remove" and contact.pk == me.pk
    with transaction.atomic():
        managers = me.company.contacts.filter(can_manage=True).exclude(pk=contact.pk)
        if action in ("remove", "revoke_manage") and contact.can_manage and not managers.exists():
            messages.error(request, "Someone has to be able to manage the company: give another contact that first.")
        elif action == "remove":
            contact.delete()
            messages.success(request, f"Removed {contact.name}.")
        elif action == "grant_manage":
            contact.can_manage = True
            contact.save(update_fields=["can_manage", "updated_at"])
        elif action == "revoke_manage":
            contact.can_manage = False
            contact.save(update_fields=["can_manage", "updated_at"])
        elif action == "make_primary":
            me.company.contacts.update(is_primary=False)
            contact.is_primary = True
            contact.save(update_fields=["is_primary", "updated_at"])
    if removing_self and not CompanyContact.objects.filter(pk=me.pk).exists():
        return redirect("portal:home")
    return redirect("portal:company-contacts", slug=me.company.slug)
