"""
Unit tests for Agentic Reasoning Intermediary.
"""

from datetime import date
import pytest
from reconcilex.core.agent.providers import MockRuleProvider
from reconcilex.core.agent.reconciler_agent import ReconcilerAgent
from reconcilex.core.models import (
    BankTransaction,
    ExtractionMethod,
    InvoiceRecord,
    MatchType,
    ReconciliationReport,
    ReconciliationSummary,
    TransactionDirection,
)


def test_agent_resolves_ambiguous_counterparty():
    # Bank record uses an abbreviated trading name
    tx = BankTransaction(
        tx_id="TX-551",
        tx_date=date(2026, 2, 16),
        counterparty="STRIPE *ACME HOSTING",
        amount=1450.00,
        direction=TransactionDirection.DEBIT,
    )
    inv = InvoiceRecord(
        doc_id="INV-991",
        source_path="/docs/acme.pdf",
        vendor_name="Acme Cloud Hosting LLC",
        invoice_date=date(2026, 2, 15),
        total_amount=1450.00,
        currency="USD",
        extraction_method=ExtractionMethod.DIGITAL_PDF,
    )

    initial_report = ReconciliationReport(
        summary=ReconciliationSummary(
            total_invoices=1,
            total_transactions=1,
            unmatched_invoices_count=1,
            unmatched_transactions_count=1
        ),
        unmatched_invoices=[inv],
        unmatched_transactions=[tx],
    )

    agent = ReconcilerAgent(provider=MockRuleProvider())
    resolved_report = agent.resolve_edge_cases(initial_report)

    assert len(resolved_report.matched_pairs) == 1
    m = resolved_report.matched_pairs[0]
    assert m.match_type == MatchType.AGENT_RESOLVED
    assert m.invoice_ids == ["INV-991"]
    assert m.tx_id == "TX-551"
    assert "AI Auditor" in m.audit_reasoning
    assert resolved_report.summary.matched_count == 1
    assert len(resolved_report.unmatched_invoices) == 0
