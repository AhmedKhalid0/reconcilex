"""
Unit tests for Deterministic Reconciliation Matching Engine.
"""

from datetime import date
import pytest
from reconcilex.core.matching.matcher import DeterministicMatcher
from reconcilex.core.models import (
    BankTransaction,
    ExtractionMethod,
    InvoiceRecord,
    MatchStatus,
    MatchType,
    TransactionDirection,
)


def _build_invoice(doc_id: str, vendor: str, amount: float, inv_date: date) -> InvoiceRecord:
    return InvoiceRecord(
        doc_id=doc_id,
        source_path=f"/fake/{doc_id}.pdf",
        vendor_name=vendor,
        invoice_date=inv_date,
        total_amount=amount,
        currency="USD",
        extraction_method=ExtractionMethod.DIGITAL_PDF,
    )


def _build_tx(tx_id: str, counterparty: str, amount: float, tx_date: date) -> BankTransaction:
    return BankTransaction(
        tx_id=tx_id,
        tx_date=tx_date,
        counterparty=counterparty,
        amount=amount,
        direction=TransactionDirection.DEBIT,
    )


def test_deterministic_exact_match():
    inv = _build_invoice("INV-101", "Vercel Inc", 250.00, date(2026, 2, 10))
    tx = _build_tx("TX-201", "VERCEL INC PAYMENTS", 250.00, date(2026, 2, 11))

    matcher = DeterministicMatcher(date_tolerance=3)
    report = matcher.reconcile([inv], [tx])

    assert len(report.matched_pairs) == 1
    m = report.matched_pairs[0]
    assert m.match_type == MatchType.EXACT_1TO1
    assert m.match_status == MatchStatus.MATCHED
    assert m.invoice_ids == ["INV-101"]
    assert m.tx_id == "TX-201"
    assert m.variance_amount == 0.0
    assert report.summary.matched_count == 1
    assert report.summary.match_rate_percentage == 100.0


def test_deterministic_bundled_subset_sum():
    inv1 = _build_invoice("INV-201", "Datadog Systems", 700.00, date(2026, 2, 12))
    inv2 = _build_invoice("INV-202", "Datadog Systems", 300.00, date(2026, 2, 14))
    tx = _build_tx("TX-301", "DATADOG SYSTEMS BATCH TRF", 1000.00, date(2026, 2, 15))

    matcher = DeterministicMatcher()
    report = matcher.reconcile([inv1, inv2], [tx])

    assert len(report.matched_pairs) == 1
    m = report.matched_pairs[0]
    assert m.match_type == MatchType.BUNDLED_1TON
    assert set(m.invoice_ids) == {"INV-201", "INV-202"}
    assert m.bank_amount == 1000.00
    assert m.invoice_total == 1000.00
    assert report.summary.unmatched_invoices_count == 0


def test_deterministic_fee_tolerance():
    inv = _build_invoice("INV-301", "International Logistics", 5000.00, date(2026, 2, 10))
    # $20 wire transfer fee deducted at settlement
    tx = _build_tx("TX-401", "INTERNATIONAL LOGISTICS WIRE", 4980.00, date(2026, 2, 12))

    matcher = DeterministicMatcher(fee_tolerance=25.0)
    report = matcher.reconcile([inv], [tx])

    assert len(report.matched_pairs) == 1
    m = report.matched_pairs[0]
    assert m.match_type == MatchType.FEE_ADJUSTED
    assert m.variance_amount == -20.00
    assert report.summary.matched_count == 1
