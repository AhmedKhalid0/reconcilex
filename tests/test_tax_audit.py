"""
Unit tests for Tax & ZATCA/VAT Compliance Auditor.
"""

from datetime import date
from pathlib import Path
import pytest
from reconcilex.core.models import ExtractionMethod, InvoiceRecord
from reconcilex.core.tax_audit import TaxAuditor


def test_tax_audit_compliant_15_percent():
    inv = InvoiceRecord(
        doc_id="INV-SA-001",
        source_path="./INV-SA-001.pdf",
        vendor_name="Riyadh Cloud Solutions",
        invoice_date=date(2026, 2, 10),
        subtotal=1000.00,
        tax_amount=150.00,
        total_amount=1150.00,
        currency="SAR",
        extraction_method=ExtractionMethod.DIGITAL_PDF
    )

    result = TaxAuditor.audit_invoice(inv)
    assert result.is_compliant is True
    assert result.detected_rate_percent == 15.0
    assert result.expected_tax == 150.00
    assert result.variance == 0.0
    assert "COMPLIANT" in result.status_label
    assert len(result.sha256_fingerprint) == 64


def test_tax_audit_detects_discrepancy():
    # Supplier incorrectly calculated 10% tax instead of 15%
    inv = InvoiceRecord(
        doc_id="INV-SA-002",
        source_path="./INV-SA-002.pdf",
        vendor_name="Erroneous Supplier Co",
        invoice_date=date(2026, 2, 12),
        subtotal=2000.00,
        tax_amount=200.00,  # Should be 300.00 at 15%
        total_amount=2200.00,
        currency="SAR",
        extraction_method=ExtractionMethod.DIGITAL_PDF
    )

    result = TaxAuditor.audit_invoice(inv)
    assert result.is_compliant is False
    assert result.variance == 100.00
    assert result.status_label == "TAX_DISCREPANCY_FLAGGED"
    assert "Tax Warning" in result.audit_notes


def test_tax_audit_zero_rated():
    inv = InvoiceRecord(
        doc_id="INV-EXEMPT-003",
        source_path="./INV-EXEMPT-003.pdf",
        vendor_name="Exempt Export Service",
        invoice_date=date(2026, 2, 15),
        total_amount=500.00,
        tax_amount=0.0,
        currency="USD",
        extraction_method=ExtractionMethod.DIGITAL_PDF
    )

    result = TaxAuditor.audit_invoice(inv)
    assert result.is_compliant is True
    assert result.status_label == "ZERO_RATED_OR_EXEMPT"
