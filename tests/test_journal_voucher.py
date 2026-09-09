"""
Unit tests for ERP Double-Entry Journal Voucher Generator.
"""

from datetime import date
import pytest
from reconcilex.core.models import (
    BankTransaction,
    MatchResult,
    MatchStatus,
    MatchType,
    ReconciliationReport,
    ReconciliationSummary,
    TransactionDirection,
)
from reconcilex.core.reporting.journal_voucher import JournalVoucherGenerator


def test_journal_voucher_balancing():
    # Fee adjusted match (20$ wire fee)
    m = MatchResult(
        match_status=MatchStatus.MATCHED,
        match_type=MatchType.FEE_ADJUSTED,
        invoice_ids=["INV-001"],
        tx_id="TX-001",
        invoice_total=5000.00,
        bank_amount=4980.00,
        variance_amount=-20.00,
    )
    # Unmatched bank withdrawal
    tx_unmatched = BankTransaction(
        tx_id="TX-999",
        tx_date=date(2026, 2, 28),
        counterparty="ATM CASH WITHDRAWAL",
        amount=300.00,
        direction=TransactionDirection.DEBIT
    )

    report = ReconciliationReport(
        summary=ReconciliationSummary(matched_count=1, unmatched_transactions_count=1),
        matched_pairs=[m],
        unmatched_transactions=[tx_unmatched]
    )

    voucher = JournalVoucherGenerator.generate_voucher(report)
    assert voucher.is_balanced is True
    assert voucher.total_debit == voucher.total_credit == 320.00
    assert len(voucher.lines) == 4

    csv_text = JournalVoucherGenerator.export_csv(voucher)
    assert "Account Code" in csv_text
    assert "5210" in csv_text
    assert "9999" in csv_text
    assert "1010" in csv_text
