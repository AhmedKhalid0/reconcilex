"""
Unit tests for Bounded Subset-Sum Combinatorial Solver.
"""

from datetime import date
import pytest
from reconcilex.core.matching.combinatorics import find_bundled_combination
from reconcilex.core.models import ExtractionMethod, InvoiceRecord


def _make_inv(doc_id: str, amount: float, vendor: str = "Acme") -> InvoiceRecord:
    return InvoiceRecord(
        doc_id=doc_id,
        source_path=f"./{doc_id}.pdf",
        vendor_name=vendor,
        invoice_date=date(2026, 2, 10),
        total_amount=amount,
        currency="USD",
        extraction_method=ExtractionMethod.DIGITAL_PDF
    )


def test_find_bundled_combination_exact_pair():
    inv1 = _make_inv("INV-001", 800.00)
    inv2 = _make_inv("INV-002", 450.00)
    inv3 = _make_inv("INV-003", 999.00)

    res = find_bundled_combination(
        target_amount=1250.00,
        candidate_invoices=[inv1, inv2, inv3],
        max_bundle_size=3
    )

    assert res is not None
    bundle, total = res
    assert len(bundle) == 2
    assert total == 1250.00
    assert {i.doc_id for i in bundle} == {"INV-001", "INV-002"}


def test_find_bundled_combination_no_match():
    inv1 = _make_inv("INV-001", 100.00)
    inv2 = _make_inv("INV-002", 200.00)

    res = find_bundled_combination(
        target_amount=500.00,
        candidate_invoices=[inv1, inv2]
    )

    assert res is None


def test_find_bundled_combination_triple():
    inv1 = _make_inv("INV-001", 150.00)
    inv2 = _make_inv("INV-002", 250.00)
    inv3 = _make_inv("INV-003", 600.00)

    res = find_bundled_combination(
        target_amount=1000.00,
        candidate_invoices=[inv1, inv2, inv3],
        max_bundle_size=3
    )

    assert res is not None
    bundle, total = res
    assert len(bundle) == 3
    assert total == 1000.00
