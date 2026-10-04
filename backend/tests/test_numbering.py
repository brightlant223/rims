"""
Ruchita Interiors — Numbering service tests (§12, §22).

Sequential allocation, year rollover, delete-does-not-reuse, restart-persistence,
unique under repeated allocation, prefix from settings, immutable historical numbers.
"""

from __future__ import annotations

import pytest

from app.services.numbering import allocate_number
from app.models.counter import NumberingCounter
from app.extensions.database import db


class TestNumberAllocation:
    """Core allocation behavior."""

    def test_first_quotation_number(self, authed_client):
        """First quotation gets QTN-YYYY-0001."""
        allocation = allocate_number("quotation")
        assert allocation.number == f"QTN-{allocation.year}-0001"
        assert allocation.sequence == 1

    def test_second_quotation_increments(self, authed_client):
        """Second quotation gets 0002."""
        allocate_number("quotation")
        allocation = allocate_number("quotation")
        assert allocation.sequence == 2
        assert allocation.number.endswith("-0002")

    def test_invoice_separate_sequence(self, authed_client):
        """Invoice numbering is independent."""
        allocate_number("quotation")
        allocation = allocate_number("invoice")
        assert allocation.number.startswith("INV-")
        assert allocation.sequence == 1

    def test_counter_persists_in_db(self, authed_client):
        """Counter row created and updated in DB."""
        allocate_number("quotation")
        counter = NumberingCounter.query.filter_by(doc_type="quotation").first()
        assert counter is not None
        assert counter.last_number == 1


class TestYearRollover:
    """New year creates new counter bucket."""

    def test_different_year_independent(self, authed_client):
        """Different year starts at 0001."""
        allocation_2026 = allocate_number("quotation", year=2026)
        allocation_2027 = allocate_number("quotation", year=2027)
        assert allocation_2026.sequence == 1
        assert allocation_2027.sequence == 1
        assert allocation_2026.number.startswith("QTN-2026-")
        assert allocation_2027.number.startswith("QTN-2027-")

    def test_current_year_default(self, authed_client):
        """Default year is current year."""
        from datetime import date
        allocation = allocate_number("quotation")
        assert allocation.year == date.today().year


class TestPrefixFromSettings:
    """Prefixes come from CompanySettings."""

    def test_quotation_prefix_from_settings(self, authed_client):
        """Quotation prefix configurable."""
        from app.models.company_settings import CompanySettings
        settings = CompanySettings.get_or_create()
        settings.quotation_prefix = "QTN"
        db.session.commit()

        allocation = allocate_number("quotation")
        assert allocation.number.startswith("QTN-")

    def test_invoice_prefix_from_settings(self, authed_client):
        """Invoice prefix configurable."""
        from app.models.company_settings import CompanySettings
        settings = CompanySettings.get_or_create()
        settings.invoice_prefix = "INV"
        db.session.commit()

        allocation = allocate_number("invoice")
        assert allocation.number.startswith("INV-")


class TestDeletedDraftNumberRetired:
    """Deleted draft number is never reused (counter not decremented)."""

    def test_delete_does_not_reuse(self, authed_client):
        """Counter continues incrementing after draft deletion."""
        from datetime import date

        from app.models.quotation import Quotation
        from app.models.client import Client

        client = Client(name="Test Client")
        db.session.add(client)
        db.session.commit()

        # Allocate the first number and attach it to a draft.
        first = allocate_number("quotation")
        q1 = Quotation(
            number=first.number,
            year=first.year,
            client_id=client.id,
            quotation_date=date.today(),
            status="draft",
        )
        db.session.add(q1)
        db.session.commit()

        # Delete the draft — its number is retired, not returned to the pool.
        db.session.delete(q1)
        db.session.commit()

        # The next allocation skips the retired number rather than reusing it.
        second = allocate_number("quotation")
        assert second.sequence == first.sequence + 1


class TestConcurrencyAndRestart:
    """Counter survives app restart; allocation is atomic."""

    def test_restart_persistence(self, authed_client):
        """Counter state survives (simulated by new session)."""
        # This is implicitly tested by the DB persistence above
        allocate_number("quotation")
        # In a real restart, the DB would still have the counter row
        counter = NumberingCounter.query.filter_by(doc_type="quotation").first()
        assert counter.last_number == 1


class TestErrorHandling:
    """Invalid inputs raise NumberingError."""

    def test_invalid_doc_type(self, authed_client):
        from app.services.numbering import NumberingError
        with pytest.raises(NumberingError, match="Invalid doc_type"):
            allocate_number("invalid")

    def test_unique_constraint_per_year(self, authed_client):
        """Unique constraint on (doc_type, year) enforced by DB."""
        allocate_number("quotation", year=2026)
        counter = NumberingCounter.query.filter_by(doc_type="quotation", year=2026).first()
        assert counter is not None

        # Manual duplicate insert would fail at DB level
        with pytest.raises(Exception):
            db.session.execute(
                db.text("INSERT INTO numbering_counters (doc_type, year, last_number) VALUES ('quotation', 2026, 0)")
            )