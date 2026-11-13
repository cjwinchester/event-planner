"""Payment processing hooks — STUB.

Sponsorships and exhibitor bookings are invoiced via a pluggable provider
(Stripe, the org's AMS, QuickBooks...). Phase 1 ships the interface and a
manual provider that only records invoices; wire a real provider by
implementing `PaymentProvider` and registering it in PROVIDERS, then make it
selectable per organization (an integrations connector with a PAYMENTS
capability is the intended home).

Webhook flow (planned): provider → POST /api/v1/payments/<provider>/webhook/
→ `provider.handle_webhook()` → Invoice.status = PAID → Sponsorship/Booking
status = PAID.
"""

from __future__ import annotations

from typing import Protocol

from django.utils import timezone

from .models import Invoice


class PaymentProvider(Protocol):
    slug: str

    def create_invoice(self, invoice: Invoice) -> Invoice:
        """Create the invoice remotely; set external_id/payment_url."""
        ...

    def handle_webhook(self, payload: dict, headers: dict) -> Invoice | None:
        """Verify and apply a provider callback; return the updated invoice."""
        ...


class ManualProvider:
    """Records invoices sent outside the system (mark paid by hand)."""

    slug = "manual"

    def create_invoice(self, invoice: Invoice) -> Invoice:
        invoice.provider = self.slug
        invoice.status = Invoice.Status.SENT
        invoice.issued_at = timezone.now()
        invoice.save(update_fields=["provider", "status", "issued_at", "updated_at"])
        return invoice

    def handle_webhook(self, payload, headers):
        raise NotImplementedError("Manual invoices have no webhooks")


PROVIDERS: dict[str, PaymentProvider] = {"manual": ManualProvider()}


def invoice_for(source, amount, provider: str = "manual") -> Invoice:
    """Create and send an invoice for a Sponsorship or ExhibitorBooking."""
    kwargs = {"sponsorship": source} if source.__class__.__name__ == "Sponsorship" else {"booking": source}
    invoice = Invoice.objects.create(event=source.event, amount=amount, **kwargs)
    return PROVIDERS[provider].create_invoice(invoice)
