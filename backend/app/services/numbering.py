"""
Ruchita Interiors — Numbering service (§12).

Allocates sequential document numbers per (doc_type, year) inside the same
transaction as the document insert. Counters are never decremented; a deleted
draft's number is retired.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.extensions.database import db
from app.models.counter import NumberingCounter, DOC_TYPES


@dataclass(frozen=True)
class NumberAllocation:
    """Result of allocating a document number."""
    number: str
    year: int
    sequence: int


class NumberingError(Exception):
    """Raised when number allocation fails."""
    pass


def _get_prefix(doc_type: str) -> str:
    """Get the prefix from company_settings (with defaults)."""
    from app.models.company_settings import CompanySettings
    settings = CompanySettings.get_or_create()
    if doc_type == "quotation":
        return settings.quotation_prefix or "QTN"
    elif doc_type == "invoice":
        return settings.invoice_prefix or "INV"
    raise ValueError(f"Unknown doc_type: {doc_type}")


def allocate_number(doc_type: str, year: int | None = None) -> NumberAllocation:
    """
    Allocate the next number for the given doc_type and year.

    Algorithm (§12):
    1. INSERT ... ON CONFLICT DO NOTHING (ensure counter row exists)
    2. UPDATE ... SET last_number = last_number + 1
    3. Read back and format '{prefix}-{year}-{nnnn}' (4-digit zero-pad)

    Must be called inside the same transaction as the document insert.
    """
    if doc_type not in DOC_TYPES:
        raise NumberingError(f"Invalid doc_type: {doc_type}")

    if year is None:
        year = date.today().year

    prefix = _get_prefix(doc_type)

    # Ensure counter row exists (INSERT ... ON CONFLICT DO NOTHING)
    # Use a raw SQL upsert for atomicity in SQLite
    db.session.execute(
        db.text("""
            INSERT INTO numbering_counters (doc_type, year, last_number, updated_at)
            VALUES (:doc_type, :year, 0, datetime('now'))
            ON CONFLICT(doc_type, year) DO NOTHING
        """),
        {"doc_type": doc_type, "year": year},
    )

    # Atomically increment and fetch
    result = db.session.execute(
        db.text("""
            UPDATE numbering_counters
            SET last_number = last_number + 1, updated_at = datetime('now')
            WHERE doc_type = :doc_type AND year = :year
            RETURNING last_number
        """),
        {"doc_type": doc_type, "year": year},
    ).fetchone()

    if result is None:
        raise NumberingError("Failed to allocate number — counter row missing after upsert")

    sequence = result[0]
    number = f"{prefix}-{year}-{sequence:04d}"

    return NumberAllocation(number=number, year=year, sequence=sequence)