"""Company pages in the portal: a contact's view of each company they work for.

A user reaches a company only through their own `CompanyContact` row; what
they see follows commerce.access: members see the company but no deals;
managers see deals with the organizations in their scope (all, if
company-wide) and manage colleagues within that scope; only company-wide
managers edit the shared company profile.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.commerce import access
from apps.commerce import portal as company_portal
from apps.commerce.claiming import claim_contacts
from apps.commerce.models import CompanyContact

from .forms import CompanyForm, ContactForm, DeliverableUploadForm, ExhibitorForm, ListingForm
from .views import _ctx


def my_contact(request, slug, *, manage: bool = False) -> CompanyContact:
    # Pick up contacts staff added since this user logged in (normally linked at login).
    if not CompanyContact.objects.filter(user=request.user, company__slug=slug).exists():
        claim_contacts(request.user)
    contact = get_object_or_404(CompanyContact.objects.select_related("company"), user=request.user, company__slug=slug)
    if manage and not contact.can_manage:
        raise PermissionDenied("Only contacts who can manage this company can do that.")
    return contact


def _ctx_company(request, me, **extra):
    return _ctx(
        request,
        me=me,
        company=me.company,
        can_edit_company=access.can_edit_company(request.user, me.company),
        **extra,
    )


@login_required
def company(request, slug):
    me = my_contact(request, slug)
    return render(request, "portal/company.html", _ctx_company(request, me, **_deals(me)))


def _deals(me):
    if not me.can_manage:
        return {"upcoming": [], "past": [], "member": True}
    today = timezone.localdate()
    rows = [{"kind": "sponsorship", "deal": d} for d in access.visible(company_portal.sponsorships(me.company), me)]
    rows += [{"kind": "booth", "deal": d} for d in access.visible(company_portal.bookings(me.company), me)]
    for r in rows:
        d = r["deal"]
        r["event"] = d.event
        r["status"] = company_portal.STATUS_LABELS.get(d.status, d.get_status_display())
        if r["kind"] == "sponsorship":
            items = list(d.deliverables.all())
            r["todo"] = sum(i.status == "pending" for i in items)
    rows.sort(key=lambda r: (r["event"].start_date, r["kind"]), reverse=True)
    return {
        "upcoming": [r for r in rows if r["event"].end_date >= today],
        "past": [r for r in rows if r["event"].end_date < today],
    }


def _listing(request, deal, editable):
    """POST handler for "how this event lists you" (shared by sponsorship and booth pages).
    Returns a redirect when it handled the request, else None."""
    action = request.POST.get("action")
    if action not in ("listing", "listing_from_company"):
        return None
    if not editable:
        raise PermissionDenied
    if action == "listing_from_company":
        deal.copy_from_company(overwrite=True)
        deal.save()
        messages.success(request, "This event now lists you as your current company profile.")
    else:
        form = ListingForm(request.POST, request.FILES, instance=deal)
        if form.is_valid():
            form.save()
            messages.success(request, f"Saved how {deal.event.name} lists you.")
        else:
            messages.error(request, "Check the listing: " + "; ".join(e for errs in form.errors.values() for e in errs))
    return redirect(request.path)


@login_required
def sponsorship(request, slug, pk):
    me = my_contact(request, slug)
    deal = get_object_or_404(access.visible(company_portal.sponsorships(me.company), me), pk=pk)
    editable = me.can_manage and company_portal.can_edit(deal.event)
    if request.method == "POST" and (handled := _listing(request, deal, editable)):
        return handled
    if request.method == "POST":
        if not editable:
            raise PermissionDenied
        item = get_object_or_404(deal.deliverables, pk=request.POST.get("deliverable"))
        form = DeliverableUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                company_portal.upload_deliverable(item, form.cleaned_data["file"])
            except company_portal.CompanyPortalError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Uploaded “{item.title}”. Thanks!")
        else:
            messages.error(request, " ".join(form.errors.get("file", ["Choose a file to upload."])))
        return redirect("portal:sponsorship", slug=slug, pk=deal.pk)
    return render(
        request,
        "portal/sponsorship.html",
        _ctx_company(
            request,
            me,
            deal=deal,
            status=company_portal.STATUS_LABELS.get(deal.status, deal.get_status_display()),
            deliverables=deal.deliverables.order_by("due_date", "pk"),
            invoices=company_portal.invoices_for(deal),
            editable=editable,
            deadline=company_portal.deadline(deal.event),
            upload_form=DeliverableUploadForm(),
            listing_form=ListingForm(instance=deal),
            today=timezone.localdate(),
        ),
    )


@login_required
def booth(request, slug, pk):
    from apps.pitches.models import FormQuestion

    me = my_contact(request, slug)
    deal = get_object_or_404(access.visible(company_portal.bookings(me.company), me), pk=pk)
    editable = me.can_manage and company_portal.can_edit(deal.event)
    if request.method == "POST" and (handled := _listing(request, deal, editable)):
        return handled
    questions = list(
        FormQuestion.objects.filter(event=deal.event, form=FormQuestion.Form.EXHIBITOR).order_by("sort_order")
    )
    initial = {f"q_{k}": v for k, v in (deal.answers or {}).items()}
    form = ExhibitorForm(request.POST or None, questions=questions, initial=initial)
    if request.method == "POST":
        if not editable:
            raise PermissionDenied
        if form.is_valid():
            try:
                company_portal.save_exhibitor_answers(deal, form.answers())
            except ValidationError as exc:
                for slug_, errs in exc.message_dict.items():
                    form.add_error(f"q_{slug_}" if f"q_{slug_}" in form.fields else None, errs)
            except company_portal.CompanyPortalError as exc:
                messages.error(request, str(exc))
                return redirect("portal:booth", slug=slug, pk=deal.pk)
            else:
                messages.success(request, "Saved your exhibitor details.")
                return redirect("portal:booth", slug=slug, pk=deal.pk)
    answers = [(q.prompt, (deal.answers or {}).get(q.slug)) for q in questions]
    return render(
        request,
        "portal/booth.html",
        _ctx_company(
            request,
            me,
            deal=deal,
            status=company_portal.STATUS_LABELS.get(deal.status, deal.get_status_display()),
            invoices=company_portal.invoices_for(deal),
            editable=editable,
            deadline=company_portal.deadline(deal.event),
            form=form,
            answers=answers,
            listing_form=ListingForm(instance=deal),
        ),
        status=422 if request.method == "POST" else 200,
    )


@login_required
def company_edit(request, slug):
    me = my_contact(request, slug)
    if not access.can_edit_company(request.user, me.company):
        raise PermissionDenied("Only company-wide managers can change the shared company profile.")
    form = CompanyForm(request.POST or None, request.FILES or None, instance=me.company)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Company profile saved.")
        return redirect("portal:company", slug=me.company.slug)
    return render(
        request,
        "portal/company_edit.html",
        _ctx_company(request, me, form=form),
        status=422 if request.method == "POST" else 200,
    )


def _can_manage_contact(me: CompanyContact, contact: CompanyContact) -> bool:
    """Company-wide managers manage anyone; scoped managers only people within their scope."""
    if me.company_wide:
        return True
    if not me.can_manage or contact.company_wide:
        return False
    mine = set(me.organizations.values_list("pk", flat=True))
    return set(contact.organizations.values_list("pk", flat=True)) <= mine


@login_required
def company_contacts(request, slug):
    me = my_contact(request, slug)
    if not me.can_manage:
        raise PermissionDenied("Only managers can see and manage the company's people.")
    form = ContactForm(request.POST or None, company=me.company, allow_company_wide=me.company_wide)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            contact = form.save(commit=False)
            contact.company = me.company
            contact.company_wide = False
            contact.save()
            access.scope_for_new_colleague(
                me, contact, company_wide=form.cleaned_data.get("company_wide", False) and contact.can_manage
            )
        messages.success(request, f"Added {contact.name}. They can log in with {contact.email} to see this company.")
        return redirect("portal:company-contacts", slug=me.company.slug)
    contacts = me.company.contacts.select_related("user").prefetch_related("organizations")
    rows = [{"c": c, "editable": _can_manage_contact(me, c)} for c in contacts]
    return render(
        request,
        "portal/company_contacts.html",
        _ctx_company(request, me, rows=rows, form=form),
        status=422 if request.method == "POST" else 200,
    )


@login_required
@require_POST
def company_contact_update(request, slug, pk):
    me = my_contact(request, slug, manage=True)
    contact = get_object_or_404(CompanyContact, pk=pk, company=me.company)
    if not _can_manage_contact(me, contact):
        raise PermissionDenied
    action = request.POST.get("action")
    removing_self = action == "remove" and contact.pk == me.pk
    with transaction.atomic():
        other_wide = me.company.contacts.filter(company_wide=True, can_manage=True).exclude(pk=contact.pk)
        other_managers = me.company.contacts.filter(can_manage=True).exclude(pk=contact.pk)
        losing = action in ("remove", "revoke_manage", "revoke_company_wide")
        if losing and contact.company_wide and contact.can_manage and not other_wide.exists():
            messages.error(request, "Make someone else company-wide first: the company needs one.")
        elif losing and action != "revoke_company_wide" and contact.can_manage and not other_managers.exists():
            messages.error(
                request, "Someone has to be able to manage the company: make another person a manager first."
            )
        elif action == "remove":
            contact.delete()
            messages.success(request, f"Removed {contact.name}.")
        elif action in ("grant_manage", "revoke_manage"):
            contact.can_manage = action == "grant_manage"
            if not contact.can_manage:
                contact.company_wide = False
            contact.save(update_fields=["can_manage", "company_wide", "updated_at"])
        elif action in ("make_company_wide", "revoke_company_wide") and me.company_wide:
            contact.company_wide = action == "make_company_wide"
            contact.can_manage = contact.can_manage or contact.company_wide
            contact.save(update_fields=["company_wide", "can_manage", "updated_at"])
        elif action == "make_primary" and me.company_wide:
            me.company.contacts.update(is_primary=False)
            contact.is_primary = True
            contact.save(update_fields=["is_primary", "updated_at"])
    if removing_self and not CompanyContact.objects.filter(pk=me.pk).exists():
        return redirect("portal:home")
    return redirect("portal:company-contacts", slug=me.company.slug)
