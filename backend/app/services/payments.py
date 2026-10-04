"""
Ruchita Interiors — payments service (§4.5, §9.2, §11).

Payment *management* is the one piece of the invoice lifecycle Phase 7 read but
never wrote. This module supplies the write side. Nothing here recalculates or
stores a status: §11 derives `payment_status` from the ledger on every read, and
`services/invoices.serialize_invoice()` already surfaces
`paid_paise` / `outstanding_paise` / `payment_status`. So "recalculate after a
mutation" is not a step in this module — it is a consequence of not caching the
answer.

The rules that matter:

- **Issued invoices only** (§11). Draft invoices have not been billed and
  cancelled ones never will be, so both are rejected. That is expressed by
  delegating to the lifecycle service rather than re-testing `status == "issued"`
  here, so the rule has one definition.
- **Overpayment is a 422 with a number in it** (§11), not a silent clamp.
- **The overpayment check runs twice.** The pre-check reads the ledger, we insert,
  then we read the ledger again. What that second read is and is not worth is
  worth being precise about, because the difference only shows up if the database
  ever changes:

  - The two reads run inside **one transaction**, so the re-read cannot see a
    payment that another transaction committed *after* this one began. It is not,
    on its own, a defence against a concurrent writer.
  - What actually prevents two concurrent payments from both landing is
    **SQLite's single-writer model**: the second writer blocks on the first's
    commit, so by the time its pre-check runs the first payment is already in the
    ledger. The guarantee is a property of the storage engine, not of this code.
  - On an MVCC engine (Postgres, MySQL) that reasoning breaks: the pre-check would
    read a snapshot taken before the competing commit, and both payments would
    pass. A port would need `SELECT ... FOR UPDATE` on the invoice row (or a
    conditional `UPDATE ... WHERE paid + ? <= grand_total`), because neither
    `with_for_update` nor the re-read is emitted meaningfully by SQLite today.

  The re-read stays: it is cheap, it is the authoritative statement of the
  total the server is about to commit, and it is the hook a real row lock would
  attach to. But it is defence-in-depth here, not the guarantee, and a rollback
  rather than a best-effort check once it fails.
"""

from __future__ import annotations

from datetime import date, datetime

from app.extensions.database import db
from app.models.invoice import Invoice, Payment
from app.services.invoices import paid_paise_for
from app.utils.errors import business_rule, not_found


def _group_indian(rupees: int) -> str:
    """Group an integer the Indian way: last 3 digits, then pairs (13.3)."""
    s = str(rupees)
    if len(s) <= 3:
        return s
    last3, rest = s[-3:], s[:-3]
    parts = []
    while len(rest) > 2:
        parts.insert(0, rest[-2:])
        rest = rest[:-2]
    if rest:
        parts.insert(0, rest)
    return ",".join(parts + [last3])


def format_rupees(paise: int) -> str:
    """Paise to a rupee string, for messages the user reads."""
    sign = "-" if paise < 0 else ""
    value = abs(int(paise))
    return f"{sign}₹{_group_indian(value // 100)}.{value % 100:02d}"


def get_payment(payment_id: int) -> Payment:
    payment = db.session.get(Payment, payment_id)
    if payment is None:
        raise not_found("Payment not found.")
    return payment


def list_payments(invoice: Invoice) -> list[Payment]:
    """
    Payment history for one invoice, newest first.

    Ordering matches `services/clients._payments_for` and is pinned on the
    relationship (`models/invoice.py`), so a client summary and the invoice
    detail can never list the same payments in a different order.
    """
    return list(invoice.payments)


def record_payment(
    invoice: Invoice, payload: dict, *, created_by: int | None = None
) -> Payment:
    """
    Record a payment against an issued invoice (§11).

    Advance, partial and exact-full are all the same operation — an amount at or
    below the outstanding. Nothing is special-cased for "full"; the status
    follows from the arithmetic.
    """
    from app.services.lifecycle import InvoiceAction, validate_invoice_transition

    allowed, reason = validate_invoice_transition(
        invoice, InvoiceAction.RECORD_PAYMENT
    )
    if not allowed:
        raise business_rule(
            reason or "Payments can only be recorded against an issued invoice."
        )

    amount = int(payload["amount_paise"])
    if amount <= 0:
        raise business_rule("Payment amount must be greater than zero.")

    # Reject a future-dated payment. The dashboard's 30-day received series and
    # monthly chart group by `paid_on`, so a payment dated next month would
    # appear as "received today" and then re-date itself to a future bucket —
    # corrupting the revenue timeline on both surfaces. Today's date is the
    # accepted ceiling; a late entry can be backdated (a payment received
    # yesterday but recorded today is still `paid_on`-today, which is fine).
    paid_on = payload["paid_on"]
    if isinstance(paid_on, str):
        # The schema passes a date object, but keep the string path defensive
        # in case a raw dict ever reaches here outside the normal route.
        try:
            paid_on = datetime.fromisoformat(paid_on).date()
        except (ValueError, TypeError):
            raise business_rule("Payment date is not a valid date.")
    if paid_on > date.today():
        raise business_rule(
            f"Payment date cannot be in the future. Use today ({date.today().isoformat()}) "
            "or an earlier date."
        )

    grand_total = invoice.grand_total_paise or 0

    # First read of the ledger: rejects the ordinary overpayment with the exact
    # §11 wording and the balance the user was actually looking at.
    outstanding = grand_total - paid_paise_for(invoice.id)
    if amount > outstanding:
        raise business_rule(
            f"Payment exceeds outstanding balance of {format_rupees(outstanding)}."
        )

    payment = Payment(
        invoice_id=invoice.id,
        amount_paise=amount,
        paid_on=paid_on,
        method=payload["method"],
        reference=payload.get("reference"),
        notes=payload.get("notes"),
        created_by=created_by,
    )
    db.session.add(payment)
    # Flush so the re-read below includes this row; nothing is committed yet.
    db.session.flush()

    # Second read of the ledger, after the insert. It is the authoritative total
    # for this commit: if the insert pushed the invoice over its grand total, the
    # whole thing rolls back rather than leaving a half-written payment behind.
    #
    # Note what this is not: the read is inside the same transaction as the
    # insert, so it cannot observe a payment another transaction committed after
    # this one started. It therefore does not, by itself, stop two concurrent
    # requests from both passing their pre-checks — SQLite's single-writer lock
    # does, by making the second writer's pre-check run after the first commit.
    # See the module docstring.
    total_after = paid_paise_for(invoice.id)
    if total_after > grand_total:
        db.session.rollback()
        raise business_rule(
            "Payment exceeds outstanding balance of "
            f"{format_rupees(grand_total - (total_after - amount))}."
        )

    db.session.commit()
    return payment


def delete_payment(payment: Payment) -> None:
    """
    Remove a payment as a correction (§3, FR-P5).

    No recalculation step: `paid_paise` and `payment_status` are derived on read,
    so the next `serialize_invoice()` of this invoice already reports the
    corrected figures. §26 records that a full audit ledger is future work, which
    is why deleting is allowed at all.

    The `RESTRICT` FK on `payments.invoice_id` is what bounds this: a payment can
    only be deleted while its invoice still exists, so this can never be used to
    strip history from a document that has been removed.
    """
    db.session.delete(payment)
    db.session.commit()
