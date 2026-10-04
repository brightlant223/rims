"""
Ruchita Interiors — Quotation/Invoice calculation service (§10).

Single source of truth for all money math. All arithmetic in integers:
- paise for amounts (1 rupee = 100 paise)
- milli-units for quantities (1 unit = 1000 milli-units)
- basis points for percentages (1% = 100 bp)

No floats at any layer. Rounding: half-up at line level and tax level.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True)
class LineCalculation:
    """Result of computing a single line total."""
    line_total_paise: int


@dataclass(frozen=True)
class TotalsCalculation:
    """Full totals breakdown for a quotation or invoice."""
    subtotal_paise: int
    discount_paise: int
    taxable_paise: int
    gst_paise: int
    grand_total_paise: int


# ---- Integer rounding helpers ----

def _round_half_up(numerator: int, denominator: int) -> int:
    """
    Half-up rounding for integer division: (numerator / denominator) rounded.
    Equivalent to: floor((numerator + denominator/2) / denominator) for positive.
    Works for negative too via Decimal.
    """
    if denominator == 0:
        return 0
    # Use Decimal for correct half-up behavior including negatives
    d = Decimal(numerator) / Decimal(denominator)
    return int(d.to_integral_value(rounding=ROUND_HALF_UP))


def calculate_line_total(qty_milli: int, rate_paise: int) -> LineCalculation:
    """
    line_total_paise = round_half_up(qty_milli * rate_paise / 1000)
    """
    if qty_milli < 0 or rate_paise < 0:
        raise ValueError("Quantity and rate must be non-negative")
    raw = qty_milli * rate_paise
    line_total = _round_half_up(raw, 1000)
    return LineCalculation(line_total_paise=line_total)


# ---- Discount calculation ----

def calculate_discount(subtotal_paise: int, discount_type: str, discount_bp: int | None, discount_fixed_paise: int | None) -> int:
    """
    discount_paise =
      percent: round_half_up(subtotal * discount_bp / 10000)
      fixed:   min(discount_fixed_paise, subtotal)  (validated > subtotal is error, not clamp)
    """
    if discount_type == "percent":
        bp = discount_bp or 0
        if bp < 0:
            raise ValueError("Discount basis points must be non-negative")
        return _round_half_up(subtotal_paise * bp, 10000)
    elif discount_type == "fixed":
        fixed = discount_fixed_paise or 0
        if fixed < 0:
            raise ValueError("Fixed discount must be non-negative")
        if fixed > subtotal_paise:
            raise ValueError("Discount cannot exceed subtotal")
        return fixed
    return 0


# ---- GST calculation ----

def calculate_gst(taxable_paise: int, gst_bp: int) -> int:
    """
    gst_paise = round_half_up(taxable_paise * gst_bp / 10000)
    """
    if gst_bp < 0 or gst_bp > 2800:
        raise ValueError("GST must be between 0 and 28% (0-2800 bp)")
    return _round_half_up(taxable_paise * gst_bp, 10000)


# ---- Full totals ----

def calculate_totals(
    line_totals: list[int],
    discount_type: str,
    discount_bp: int | None,
    discount_fixed_paise: int | None,
    gst_bp: int,
    other_charges_paise: int,
) -> TotalsCalculation:
    """
    Compute all totals from line items and document-level fields.

    Formulas (§10.2):
      subtotal_paise    = Σ line_total_paise
      discount_paise    = percent: round_half_up(subtotal × discount_bp / 10000)
                          fixed:   discount_fixed_paise (validated ≤ subtotal)
      taxable_paise     = subtotal_paise - discount_paise
      gst_paise         = round_half_up(taxable_paise × gst_bp / 10000)
      grand_total_paise = taxable_paise + gst_paise + other_charges_paise
    """
    subtotal = sum(line_totals)

    discount = calculate_discount(subtotal, discount_type, discount_bp, discount_fixed_paise)
    taxable = subtotal - discount
    gst = calculate_gst(taxable, gst_bp)
    other_charges = max(0, other_charges_paise or 0)
    grand_total = taxable + gst + other_charges

    return TotalsCalculation(
        subtotal_paise=subtotal,
        discount_paise=discount,
        taxable_paise=taxable,
        gst_paise=gst,
        grand_total_paise=grand_total,
    )


# ---- UPI intent URIs ----
#
# §8.5. Built here, beside the money arithmetic and for the same reason: this module
# is the one place allowed to decide what a number means, and a URI that asks for a
# different figure than the document displays is a collection bug.

#: The only currency this app issues documents in. Fixed, not configurable.
UPI_CURRENCY = "INR"

#: UPI scheme prefix defined by NPCI.
UPI_SCHEME = "upi://pay"

#: UPI apps truncate long notes, and a longer payload needs a denser QR. The invoice
#: number is what makes a note useful to a payer, so the tail is kept.
UPI_NOTE_MAX_LENGTH = 64


def format_upi_amount(paise: int) -> str:
    """
    Integer paise as the plain decimal string UPI expects ("600050" -> "6000.50").

    No currency symbol and no thousands separators, because `am` is a machine-readable
    number. Integer arithmetic throughout: `paise / 100` in Python would give
    "6000.5" or, for values that are not exactly representable in binary floating
    point, something like "6000.499999999999" — a QR that asks for a fraction of a
    paisa less than the invoice, which no reviewer would spot by eye.

    Non-positive and non-integer values clamp to "0.00" rather than raising, so a bad
    input produces a caller that declines to build a URI (see `build_upi_uri`) instead
    of a 500 mid-request.
    """
    try:
        value = int(paise)
    except (TypeError, ValueError):
        return "0.00"
    if value <= 0:
        return "0.00"
    return f"{value // 100}.{value % 100:02d}"


def _clean_upi_param(value: str | None) -> str:
    """
    Keep a query parameter's characters safe and useful.

    Vendor names legitimately contain `.`, `-`, `&`, `/` and spaces ("Ruchita & Co",
    "M/s Verma"), so those survive — `encodeURIComponent` handles them. Only control
    characters are dropped, and runs of whitespace collapsed, so a hostile or
    accidental value cannot inject extra URI parameters.
    """
    if value is None:
        return ""
    return " ".join(str(value).split())


def build_upi_uri(
    *, vpa: str | None, payee_name: str | None = None, amount_paise: int, note: str | None = None
) -> str | None:
    """
    Build a UPI payment intent URI, or None when there is nothing to ask for.

    Returns `None` — rather than a URI with an empty or zero `am` — when the VPA is
    missing or malformed, or the amount is not positive. That distinction matters to
    the caller: "no QR configured" is a normal state that falls back to showing the
    UPI ID as text, while a QR that opens a payment app asking for ₹0 reads as a
    broken feature to a customer.

    `vpa` is validated only as "contains an @" rather than against a provider
    allow-list, for the same reason the settings validator is permissive: a VPA's
    provider suffix is not an enumerable set, and rejecting a legitimate one would
    block a real payment.
    """
    address = _clean_upi_param(vpa)
    if not address or "@" not in address:
        return None

    try:
        if int(amount_paise) <= 0:
            return None
    except (TypeError, ValueError):
        return None

    params = [
        ("pa", address),
        ("pn", _clean_upi_param(payee_name) or "Merchant"),
        ("am", format_upi_amount(amount_paise)),
        ("cu", UPI_CURRENCY),
    ]

    cleaned_note = _clean_upi_param(note)
    if cleaned_note:
        params.append(("tn", cleaned_note[:UPI_NOTE_MAX_LENGTH]))

    from urllib.parse import quote

    query = "&".join(f"{key}={quote(value, safe='')}" for key, value in params)
    return f"{UPI_SCHEME}?{query}"


# ---- Validation helpers ----

def validate_document(
    line_items: list[dict],
    discount_type: str,
    discount_bp: int | None,
    discount_fixed_paise: int | None,
    gst_bp: int,
    other_charges_paise: int,
) -> list[str]:
    """Return list of document-level validation errors (empty if valid)."""
    errors = []

    # At least one valid line required for Send
    valid_lines = [item for item in line_items if item.get("name", "").strip() and item.get("qty_milli", 0) > 0 and item.get("rate_paise", 0) > 0]
    if not valid_lines:
        errors.append("At least one valid item (name, quantity, rate) is required")

    if discount_type not in ("percent", "fixed"):
        errors.append("Discount type must be 'percent' or 'fixed'")

    if discount_type == "percent":
        bp = discount_bp or 0
        if bp < 0:
            errors.append("Discount percent cannot be negative")
    elif discount_type == "fixed":
        fixed = discount_fixed_paise or 0
        if fixed < 0:
            errors.append("Fixed discount cannot be negative")
        else:
            # A fixed discount larger than the subtotal is a validation error
            # (§10.3), not a 500. Compute the subtotal with the same line math
            # the recompute step uses so this check matches the stored totals.
            # `calculate_discount` still guards this as defence-in-depth, but by
            # validating here the API returns 422 VALIDATION_ERROR instead of
            # letting a bare ValueError fall through to the 500 handler.
            subtotal = sum(
                calculate_line_total(
                    max(0, item.get("qty_milli", 0)),
                    max(0, item.get("rate_paise", 0)),
                ).line_total_paise
                for item in line_items
            )
            if fixed > subtotal:
                errors.append("Discount cannot exceed subtotal")

    if gst_bp < 0 or gst_bp > 2800:
        errors.append("GST must be between 0% and 28%")

    if other_charges_paise is not None and other_charges_paise < 0:
        errors.append("Other charges cannot be negative")

    return errors