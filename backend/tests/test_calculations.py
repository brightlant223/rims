"""
Ruchita Interiors — Calculation service tests (§10, §22).

Exact paise assertions for line totals, discount rounding, GST, and grand total.
Property-style edge cases included.
"""

from __future__ import annotations

import pytest

from app.services.calculations import (
    calculate_line_total,
    calculate_discount,
    calculate_gst,
    calculate_totals,
    validate_document,
)


class TestLineTotal:
    """Line total = round_half_up(qty_milli * rate_paise / 1000)."""

    def test_basic_integer(self):
        calc = calculate_line_total(1000, 10000)  # 1.000 * 100.00 = 100.00
        assert calc.line_total_paise == 10000

    def test_three_decimal_qty(self):
        calc = calculate_line_total(1500, 20000)  # 1.500 * 200.00 = 300.00
        assert calc.line_total_paise == 30000

    def test_round_half_up(self):
        # 1.001 * 100.00 = 100.10 (no rounding needed)
        calc = calculate_line_total(1001, 10000)
        assert calc.line_total_paise == 10010

        # 1.000 * 100.05 = 100.05 (rate has paise)
        calc = calculate_line_total(1000, 10005)
        assert calc.line_total_paise == 10005

    def test_milli_rounding(self):
        # 1.111 * 100.00 = 111.10 (111100 / 1000 = 111.1)
        calc = calculate_line_total(1111, 10000)
        assert calc.line_total_paise == 11110

    def test_zero_qty_or_rate(self):
        assert calculate_line_total(0, 10000).line_total_paise == 0
        assert calculate_line_total(1000, 0).line_total_paise == 0

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            calculate_line_total(-1, 10000)
        with pytest.raises(ValueError):
            calculate_line_total(1000, -1)


class TestDiscount:
    """Percent: round_half_up(subtotal * bp / 10000). Fixed: exact value."""

    def test_percent_basic(self):
        # 10000 paise * 500 bp = 500 paise (5%)
        assert calculate_discount(10000, "percent", 500, None) == 500

    def test_percent_round_half_up(self):
        # 10000 * 333 = 3330000 / 10000 = 333 (3.33% of 100)
        assert calculate_discount(10000, "percent", 333, None) == 333

        # 10000 * 334 = 3340000 / 10000 = 334
        assert calculate_discount(10000, "percent", 334, None) == 334

    def test_percent_zero(self):
        assert calculate_discount(10000, "percent", 0, None) == 0

    def test_fixed_basic(self):
        assert calculate_discount(10000, "fixed", None, 500) == 500

    def test_fixed_equals_subtotal(self):
        # Fixed discount equal to subtotal is allowed (results in 0 taxable)
        assert calculate_discount(10000, "fixed", None, 10000) == 10000

    def test_fixed_exceeds_subtotal_raises(self):
        with pytest.raises(ValueError, match="exceed subtotal"):
            calculate_discount(10000, "fixed", None, 10001)

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            calculate_discount(10000, "percent", -1, None)
        with pytest.raises(ValueError):
            calculate_discount(10000, "fixed", None, -1)


class TestGST:
    """GST = round_half_up(taxable * bp / 10000). Range 0-2800 bp."""

    def test_basic(self):
        # 10000 * 1800 = 18000000 / 10000 = 1800 (18%)
        assert calculate_gst(10000, 1800) == 1800

    def test_zero_gst(self):
        assert calculate_gst(10000, 0) == 0

    def test_max_gst(self):
        # 10000 * 2800 = 28000000 / 10000 = 2800 (28%)
        assert calculate_gst(10000, 2800) == 2800

    def test_out_of_range_raises(self):
        with pytest.raises(ValueError):
            calculate_gst(10000, -1)
        with pytest.raises(ValueError):
            calculate_gst(10000, 2801)


class TestFullTotals:
    """Integration: subtotal → discount → taxable → gst → grand."""

    def test_percent_discount(self):
        lines = [10000, 20000]  # 30000 subtotal
        totals = calculate_totals(
            line_totals=lines,
            discount_type="percent",
            discount_bp=1000,  # 10%
            discount_fixed_paise=None,
            gst_bp=1800,  # 18%
            other_charges_paise=0,
        )
        # subtotal = 30000
        # discount = round(30000 * 1000 / 10000) = 3000
        # taxable = 27000
        # gst = round(27000 * 1800 / 10000) = 4860
        # grand = 27000 + 4860 = 31860
        assert totals.subtotal_paise == 30000
        assert totals.discount_paise == 3000
        assert totals.taxable_paise == 27000
        assert totals.gst_paise == 4860
        assert totals.grand_total_paise == 31860

    def test_fixed_discount(self):
        lines = [10000, 20000]
        totals = calculate_totals(
            line_totals=lines,
            discount_type="fixed",
            discount_bp=None,
            discount_fixed_paise=5000,
            gst_bp=1800,
            other_charges_paise=0,
        )
        # subtotal = 30000
        # discount = 5000
        # taxable = 25000
        # gst = round(25000 * 1800 / 10000) = 4500
        # grand = 25000 + 4500 = 29500
        assert totals.subtotal_paise == 30000
        assert totals.discount_paise == 5000
        assert totals.taxable_paise == 25000
        assert totals.gst_paise == 4500
        assert totals.grand_total_paise == 29500

    def test_other_charges(self):
        lines = [10000]
        totals = calculate_totals(
            line_totals=lines,
            discount_type="percent",
            discount_bp=0,
            discount_fixed_paise=None,
            gst_bp=1800,
            other_charges_paise=500,
        )
        # subtotal = 10000
        # discount = 0
        # taxable = 10000
        # gst = 1800
        # grand = 10000 + 1800 + 500 = 12300
        assert totals.grand_total_paise == 12300

    def test_zero_lines(self):
        totals = calculate_totals(
            line_totals=[],
            discount_type="percent",
            discount_bp=0,
            discount_fixed_paise=None,
            gst_bp=1800,
            other_charges_paise=0,
        )
        assert totals.subtotal_paise == 0
        assert totals.grand_total_paise == 0


class TestValidation:
    """Line and document validation."""

    def test_document_valid(self):
        items = [{"name": "A", "qty_milli": 1000, "rate_paise": 10000}]
        errors = validate_document(
            items, "percent", 1000, None, 1800, 0
        )
        assert errors == []

    def test_document_no_valid_items(self):
        items = [{"name": "", "qty_milli": 0, "rate_paise": 0}]
        errors = validate_document(
            items, "percent", 1000, None, 1800, 0
        )
        assert any("valid item" in e for e in errors)

    def test_document_invalid_gst(self):
        items = [{"name": "A", "qty_milli": 1000, "rate_paise": 10000}]
        errors = validate_document(
            items, "percent", 1000, None, 3000, 0
        )
        assert any("GST must be between" in e for e in errors)

    def test_document_negative_other_charges(self):
        items = [{"name": "A", "qty_milli": 1000, "rate_paise": 10000}]
        errors = validate_document(
            items, "percent", 1000, None, 1800, -1
        )
        assert any("Other charges cannot be negative" in e for e in errors)